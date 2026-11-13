# Revisión independiente de los experimentos POST

Fecha: 12 de noviembre de 2026. Base examinada: `42f8234fed9f5a46070b1a102d12afc20a36e27f`. Alcance: archivos nuevos de `experiments/` y `tests/test_post_experiments.py`; dependencias directas de DICOM, volumen, registro, modelos y almacenamiento. No se modificó código durante esta revisión.

## Dictamen

**Los dos hallazgos P2 quedan cerrados tras corrección y verificación independiente.** La revisión permite continuar con integración y experimentos; siguen pendientes el rebuild Docker y una ejecución real completa del CLI. No se detectó un cambio del algoritmo servido ni una conversión de predicciones en referencias clínicas. La separación del runner y sus candidatas es adecuada.

| Prioridad inicial | Cantidad | Descripción |
| --- | ---: | --- |
| P0/P1 | 0 | No identificados en este alcance. |
| P2 | 2 | Orden de verificación SEG y comparación de retículas en gráficos. |

No se ejecutó CodeQL, Semgrep ni una herramienta de cobertura. La revisión fue manual, con inspección de historia y dependencias y ejecución de pruebas sintéticas. No equivale a auditar todas las librerías externas ni a validar anatomía.

## 1. P2 — Fijar el hash antes de releer la cabecera SEG

**Ubicación inicial:** `experiments/post_tracking.py:59–66`, función `read_sources`. Hash inicial del archivo: `851bd32e45bc23f54936e2d6db2b628e5ea8096eff8dc51b7df39a77e4bb618c`.

La versión revisada llama `verify_pre_sources` y conserva su cabecera antes de calcular los hashes. Si el SEG es reemplazado entre esas operaciones, `referenced_mask` puede utilizar la cabecera anterior para interpretar los píxeles del archivo nuevo. El hash registrado ya sería el nuevo y podría permanecer estable, de modo que la comprobación posterior de bytes no descubre la mezcla.

Es un riesgo de procedencia ante modificación concurrente del archivo de origen; no presupone un atacante remoto ni afirma que haya ocurrido en los pilotos. Montar `/data` de solo lectura en el contenedor impide escribir desde él, pero no impide que el host sustituya un archivo.

**Corrección acotada:** seleccionar SEG, fijar hashes CT/SEG, releer/verificar cabeceras, extraer píxeles y comparar nuevamente los hashes. Es el orden ya utilizado por `Lab.create_pre_edit` en la base (`hcclab/lab.py:163–172`). Añadir una prueba de sustitución durante la verificación, que deba abortar antes de producir un resultado atribuible a fuentes inconsistentes.

**Estado: corregido y verificado.** `read_sources` ahora fija los hashes en las líneas 62–63, verifica la cabecera en 66–67 y vuelve a comprobar bytes antes de retornar en 91. La regresión sintética de sustitución SEG aprobó en una ejecución independiente de la suite.

## 2. P2 — No comparar adquisiciones distintas porque tengan igual número de cortes

**Ubicación inicial:** `experiments/plot_tracking.py:26–49` y `:60–61`. Hash inicial: `3f34188588fc472ea11039c9c18e9f9070acc9d4895a6cd8757b12b6274ab13e`.

`read_report` devuelve caso, variante, índices y áreas, pero descarta la identidad de la retícula. `main` solo exige mismo caso e igual secuencia de índices. Dos adquisiciones POST del mismo paciente con el mismo número de cortes pasarían esa condición aunque sus planos, espaciado o contenido difieran. Las curvas se presentarían como variantes de una misma adquisición.

**Corrección acotada:** conservar y comparar `manifest.sources.post.stack_identity`, junto con serie/adquisición explícitas; verificar también geometría y número de cortes declarados. Los informes antiguos necesitan una procedencia equivalente comprobable o una limitación explícita: no asumir equivalencia porque coincidan caso y longitud. Añadir una prueba con igual caso/longitud y distinta retícula.

Este hallazgo no invalida los gráficos históricos actuales, cuya fuente común fue verificada durante los pilotos. Afecta al contrato general del script reutilizable.

**Estado: corregido y verificado.** La nueva versión requiere informes con manifiesto, conserva UID/adquisición y el bloque POST completo —incluidos retícula, geometría y hashes CT— y los compara en `require_same_acquisition`. Se retiró el soporte de informes antiguos sin esa evidencia. Tres pruebas independientes aprobaron, incluida la negativa con mismo caso/longitud pero distinta serie, adquisición, espaciado o bytes.

## Aspectos comprobados

- `selected_stack` exige que la serie pertenezca al caso y a la etapa cronológica; selecciona por `AcquisitionNumber` y rechaza coincidencias ambiguas. Esto no verifica la fase de contraste, limitación que la documentación declara.
- `required_segment` comprueba número y etiqueta `Mass`/`Liver`. `referenced_mask` conserva las referencias SOP y la geometría; `load_hu_volume` comprueba estudio, serie, píxeles, orientación, posiciones y HU actuales.
- El recorte local afecta XY de la **imagen de entrada**, conserva todos los cortes z y reinserta la salida en la forma nativa. Fuera de ese campo hay regiones no evaluadas, declaradas expresamente. No se recorta el resultado a PRE para hacerlo pasar.
- La ventana no modifica el volumen original; padding queda fuera de la salida. Los offsets XY y el espaciado anisótropo tienen pruebas significativas.
- `infer_raw` comprueba logits finitos, índices válidos y cobertura de todos los cortes. Reinicia el predictor entre direcciones y al terminar. El límite temporal es cooperativo entre cortes; no interrumpe un kernel bloqueado.
- El bypass de la regla de mayoría fuera de ROI es deliberado y aparece en el informe. `CandidateStore` no recibe las máscaras experimentales. Dice/IoU entre variantes llevan `not_accuracy: true`; los conteos HU no se etiquetan como viabilidad ni Lipiodol.
- El runner verifica configuración y pesos fijados antes de inferir, reutiliza el cargador existente y no descarga modelos durante el experimento. La implementación upstream examinada carga con `weights_only=True`. No se auditó exhaustivamente el contenido del paquete externo.
- `safe_output_directory` resuelve rutas y separa salidas de datos, código y `/annotations`; crea un directorio nuevo con UUID. Las variantes duplicadas se rechazan desde CLI. No encontré una ruta normal que sobrescriba resultados de otra ejecución.
- Los JSON de desarrollo distinguen resultados históricos de pilotos de resultados del nuevo runner. El nuevo CLI todavía requiere una ejecución real completa: los ensayos anteriores no demuestran que ya se haya ejecutado esa ruta.

## Pruebas y huecos específicos

Se ejecutó el comando documentado en una instancia de la imagen existente, montando `experiments` y `tests` de solo lectura. **Las 13 pruebas presentes en la primera pasada aprobaron**, sin pesos, modelos ni datos médicos. La prueba de inferencia usa un predictor simulado: comprueba contrato, cobertura, padding y liberación de estado, no el comportamiento de la red.

Durante la revisión se añadió una prueba de registro hepático sintético con ejes rotados y traslación conocida. Fue inspeccionada; su ejecución y la de las nuevas regresiones deben constar en el cierre para no atribuirlas a aquella primera pasada.

Huecos pendientes al inicio:

1. Sustitución de SEG durante la captura de procedencia.
2. Rechazo de retículas distintas en el generador de gráficos.
3. Ejecución integral del CLI nuevo desde CT/SEG hasta NPZ/JSON y comparación de manifiestos. El predictor simulado no ejercita `Frames` ni el redimensionado real de la red.
4. Integración de `experiments` en la imagen que copia también los tests. Al revisar, `Dockerfile` todavía copiaba `tests` sin copiar `experiments`; la suite de una imagen reconstruida sin los montajes de revisión fallaría al importar el test nuevo. El responsable de integración ya tiene este ajuste pendiente.

## Historia, alcance y dependencias

Los archivos de experimentación no existían en la base; son añadidos sin commit al momento de esta revisión. Por ello no hay líneas eliminadas sobre las que ejecutar `blame` en esos archivos. Se contrastaron los contratos con la historia de `hcclab`: el rechazo por deriva procede de `42f8234`; cargador/modelo automático, de `2c2a842`; controles de fuentes PRE, de `4931339` y cambios posteriores. No se cambió de checkout ni se modificó la base.

Archivos examinados: `tracking_core.py`, `post_tracking.py`, `plot_tracking.py`, `__init__.py`, `manifest.example.json`, `development_results.json`, `README.md`, `ABLATION.md`, `requirements-figures.txt`, `tests/test_post_experiments.py` y `tests/test_tracking_plot.py`. Plotting y sus pruebas aparecieron durante la revisión y se incorporaron al alcance.

Radio de impacto directo acotado: `read_sources`, `refine_with_liver`, `load_predictor` e `infer_raw` tienen cada uno un sitio de llamada en el runner; `read_report` tiene uno en plotting. Hay llamadas adicionales desde tests. No aparecen invocaciones nuevas desde el servidor ni la UI. La reutilización de helpers privados crea dependencia de sus contratos; los hashes del manifiesto permiten detectar versiones distintas, pero no sustituyen pruebas de compatibilidad.

**Confianza:** alta en los dos defectos de flujo identificados y en los contratos sintéticos examinados; limitada respecto de inferencia real, anatomía y dependencias externas. No se reporta un porcentaje de cobertura de líneas porque no fue medido.

## Cierre

Segunda pasada: **15/15 pruebas aprobaron** en Docker, sin modelos ni datos médicos. Incluyen sustitución de SEG durante la verificación y registro sintético con traslación/ejes rotados; estas dos pruebas son adicionales a las 13 iniciales. También se ejecutaron **3/3 pruebas de plotting**, mediante `unittest` local, sin Matplotlib ni imágenes: identidad de adquisición, cobertura de cortes vacíos y deduplicación del corte inicial bidireccional.

Los dos hallazgos quedan cerrados. Se comprobó la adición `COPY experiments ./experiments` en `Dockerfile:21`; reconstrucción y suite integral de esa imagen siguen a cargo de integración. La ejecución real completa del CLI nuevo permanece pendiente. No se ejecutó inferencia de modelos ni se volvió a medir un caso clínico durante esta revisión. Corregir estos defectos de software no demuestra calidad de segmentación.

Hashes de cierre de las correcciones verificadas:

- `experiments/post_tracking.py`: `bbfb57c97afa345428e24fcbbc6428436c3179ad011d8eb804d1d34fce5767be`.
- `experiments/plot_tracking.py`: `65581a3182940d9e7095416f29bbb4aa87d81981cd582336657e29214cd7773e`.

Los números de línea de los hallazgos describen la versión inicial, identificada por su hash; los archivos nuevos aún no tenían commit propio en esta revisión.
