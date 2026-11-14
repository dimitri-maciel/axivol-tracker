# Refinamiento neuronal 3D de la candidata POST

**Estado: protocolo predeclarado el 13 de noviembre de 2026; HCC_006 y el control HCC_002 ejecutados localmente. No se adopta como default.** nnInteractive recibió la máscara POST del protocolo 2.1 como entrada inicial. HCC_006 muestra un cambio parcial de la transición señalada, sin una corrección completa convincente; el control muestra cambios extensos en sus extremos y nueva fragmentación en algunos cortes. No cambia el método de la aplicación ni su candidata activa. La motivación es estudiar la discontinuidad observada en los cortes 55–56 mediante un modelo volumétrico, después de las [tres comparaciones anteriores](post-prompt-conditioning.md), sin forzar un relleno.

## Pregunta y diferencia frente al ensayo anterior

¿Un refinador 3D condicionado por la candidata POST completa puede producir límites más coherentes con la imagen y con los cortes vecinos, sin introducir nuevas extensiones, pérdidas o fragmentación importantes?

Los pilotos nnInteractive previos recibían una sola caja obtenida del registro PRE. Produjeron salidas muy sensibles a esa caja; el ensayo con registro hepático tuvo una prolongación distante. **Esta prueba cambia el tipo de entrada:** proporciona la máscara POST ya calculada por Axivol, junto con los HU POST nativos, a la función oficial `add_initial_seg_interaction(initial_seg, run_prediction=True)`. No repite la prueba de caja ni traslada una máscara PRE al POST.

El código coloca la máscara inicial en el canal de segmentación previa, reinicia las otras interacciones, prepara el contexto de esa estructura y llama a `_predict(force_full_refine=True)`. La planificación cubre la estructura inicial completa, no solo los píxeles que hayan cambiado. La configuración heredada del checkpoint v1 declara soporte para etiqueta inicial y refinamiento sin nuevas interacciones. **Pasar `run_prediction=False` solo inicializaría el estado y no sería este experimento.** [API fijada](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/nnInteractive/inference/inference_session.py#L1598-L1644), [planificación del refinamiento](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/nnInteractive/inference/inference_session.py#L2168-L2208).

La operación es automática a partir de una entrada automática. Sin embargo, **no constituye una segunda segmentación independiente**: hereda información y posibles errores de la candidata inicial. Tampoco descubre todas las lesiones del estudio. La red trabaja con parches y contexto alrededor del objetivo; recibir el volumen completo no convierte sus ceros restantes en ausencia de lesión comprobada. La API devuelve una envolvente de regiones escritas, no un mapa exacto de cobertura de inferencia por vóxel. Fuera de esa envolvente se exigirá igualdad con el baseline y se registrará `inherited_baseline_not_reevaluated`; no se presentará esa zona como predicción nueva del refinador.

## Casos e información que se mantiene fija

| Caso | Fuente histórica para localizar | Imagen para este refinamiento | Baseline POST congelado |
| --- | --- | --- | --- |
| HCC_006 | PRE adquisición 3; `Mass` segmento 2 y `Liver` segmento 1 publicados | POST adquisición 1; 103 × 512 × 512 | `run-20261113T211925-17cd15d6/refined-2mm.npz`, 11 355 vóxeles |
| HCC_002 | PRE adquisición 1; `Mass` segmento 2 y `Liver` segmento 1 publicados | POST adquisición 1; 97 × 512 × 512 | `run-20261113T212052-93e1217e/refined-2mm.npz`, 413 118 vóxeles |

Las rutas corresponden a `output/post-boundary/`, fuera de Git. Los manifiestos determinan las series UID, adquisiciones, orden de SOP, geometría y hashes concretos. El nombre de caso y el recuento de vóxeles no bastan para identificar una entrada. La máscara activa HCC_006 `25b9f264bf5b40dfa2dfc54cb4b30f6b` fue verificada como equivalente al baseline, pero el experimento lee el artefacto congelado para no depender de una selección cambiante de la interfaz.

El refinador recibe exclusivamente:

- TC POST en HU, `float32`, con todos los cortes de la adquisición y orden interno ZYX.
- Máscara POST inicial binaria en esa misma retícula nativa.
- Configuración y pesos fijos del modelo.

No recibe la máscara PRE ni un volumen tumoral esperado. No se recalcula el registro, no se dibujan cajas o puntos nuevos sobre el POST, no se aplica W400/L40 a la entrada de nnInteractive, y no se convierte la TC a 8 bits. Su [documentación oficial](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/readme.md) pide entregar las intensidades originales. La ventana solo se usará al visualizar. Los índices de imagen, prompts y salida compartirán el mismo orden; el espaciado y la geometría DICOM se conservarán para la trazabilidad y las mediciones.

El adaptador no realiza normalización física del espaciado ni remuestreo externo. La máscara de validez comprueba que el baseline y la salida no contengan foreground en padding; **no es un canal que reciba la red**. Las intensidades nativas de píxeles inválidos se mantienen como en el piloto previo, y esa decisión queda registrada. No se debe afirmar que nnInteractive las excluye internamente.

No habrá recorte final a la extensión z del baseline, intersección con el hígado, selección de la componente mayor, cierre de huecos, erosión ni reglas especiales para el corte 55. La predicción completa se conserva, incluidas sus extensiones o fragmentos. Un fallo de ejecución, una salida vacía o una geometría incompatible se registran como tales; no se sustituyen por una máscara que parezca funcionar.

## Artefacto y entorno

Se reutiliza el [dossier existente](model-provenance/nninteractive.json), sin descargar otro modelo ni atribuir al artículo propiedades no verificadas del checkpoint.

| Elemento | Identificador comprobado |
| --- | --- |
| Código nnInteractive | Commit `e3126a029f46dcfd798c8428eec292f52cd438d6`; `pyproject.toml` declara paquete **2.6.0** |
| Pesos | Modelo **v1.0**, fold 0; esto no significa paquete Python v1.0 |
| Revisión de pesos HF | `3f308d751c00644e4fde6f09c600264b393b21b5` |
| Archivo | `nnInteractive_v1.0/fold_0/checkpoint_final.pth` |
| Tamaño | 411 387 150 bytes |
| SHA-256 | `b3ac4421f85457bbd1aa0d87f5e67bcb7bc8e2ce6b824b6ac45077cc5d630ea9` |
| Código | [Apache-2.0 en el commit fijado](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/LICENSE) |
| Pesos | [CC BY-NC-SA 4.0 en el artefacto fijado](https://huggingface.co/MIC-DKFZ/nnInteractive/blob/3f308d751c00644e4fde6f09c600264b393b21b5/nnInteractive_v1.0/LICENSE) |

El entorno de investigación previamente preparado usa Torch 2.5.1, nnU-Net 2.7.0, `dynamic-network-architectures` 0.4.4, NumPy 2.5.4 y SciPy 1.18.1. La ejecución nueva debe registrar los paquetes realmente importados, hashes del código cargado, checkpoint y los cuatro metadatos auxiliares del modelo; una ruta local de instalación no sustituye un pin. Se verificará la capacidad de etiqueta inicial sin forzar overrides.

Contrato del adaptador: `refine_with_nninteractive(post_hu, initial_mask, spacing_zyx, *, model_dir, valid_mask=None)`. Configuración declarada: CPU, dos hilos de Torch, `do_autozoom=True`, compilación desactivada, interacciones comprimidas con `blosc2`, deshacer desactivado y fold 0. No añade TTA, nuevos prompts ni una semilla aleatoria elegida después de observar resultados; el loader pone la red en evaluación. Esto no constituye una promesa de igualdad binaria entre equipos o versiones. Se ejecutará una corrida a la vez, en proceso aislado con cuota de dos CPU y 7 GiB, datos de solo lectura y salidas fuera del código y de la aplicación.

Los pilotos anteriores de una caja consumieron alrededor de 6,3 GiB y cuatro minutos; con 6 GiB hubo OOM y con 7 GiB terminaron. **Esos valores no predicen el coste del refinamiento completo**: el número y tamaño de los parches pueden cambiar, especialmente en HCC_002. El nuevo tiempo y pico RSS deben medirse. Si no cabe, se registra el límite; no se reduce silenciosamente la resolución, la estructura o la cantidad de cortes.

El artículo [nnInteractive: Redefining 3D Promptable Segmentation](https://doi.org/10.48550/arXiv.2503.08373), arXiv v1, describe entrenamiento con más de 120 conjuntos volumétricos. No está establecida la lista completa de desarrollo del artefacto local ni su separación de HCC-TACE-Seg. El [verificador del dossier](model-provenance/nninteractive.gate.json) mantiene esas incertidumbres; este brazo no puede presentarse como validación externa independiente. Además, la dependencia de la máscara Axivol inicial impide interpretar su acuerdo con Axivol como exactitud.

## Qué calcula y cómo leer los números

Sea \(I[z,y,x]\) la TC POST en HU y \(M_0[z,y,x]\in\{0,1\}\) su candidata inicial. El experimento calcula conceptualmente

\[
M_1=F_\theta(I,M_0),
\]

donde \(\theta\) son los pesos fijados; no se entrenan con estos dos casos. Esta notación incluye el preprocesamiento, AutoZoom, selección de parches, red y escritura oficial. **No significa que la red evalúe cada vóxel de todo el estudio.** La máscara inicial condiciona la predicción; un cambio pequeño puede ser conservación útil o conservación de un error previo.

La normalización oficial usa \(I'=(I-\mu_R)/s_R\): calcula media y desviación estándar de la **caja envolvente de intensidades no nulas**, incluidos los ceros interiores a esa caja, y aplica esa transformación al volumen completo. No extrae una región tumoral ni mide viabilidad; tampoco recorta físicamente la TC a esa caja. No es la ventana W400/L40 del visor. Si la desviación es cero, la transformación no está definida. Véase el [preprocesamiento fijado](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/nnInteractive/inference/inference_session.py#L1060-L1100).

En cada llamada de parche, el código oficial obtiene puntuaciones de clase \(s_c(p)\) y toma \(\arg\max_{c\in\{0,1\}}s_c(p)\). Después aplica sus operaciones de retorno y escritura del parche; no se reemplazan por un umbral añadido por Axivol. `argmax` elige una clase, no proporciona una probabilidad clínica calibrada. La [predicción inicial](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/nnInteractive/inference/inference_session.py#L1770-L1838) y la [predicción de refinamiento](https://github.com/MIC-DKFZ/nnInteractive/blob/e3126a029f46dcfd798c8428eec292f52cd438d6/nnInteractive/inference/inference_session.py#L2034-L2068) permiten comprobar el orden. Si \(E\) es la envolvente de escritura reportada, el adaptador verifica \(M_1(p)=M_0(p)\) para todo \(p\notin E\). No supone que todo \(p\in E\) haya sido reevaluado.

Con espaciado \((\Delta z,\Delta y,\Delta x)\) en milímetros y máscaras en la retícula POST nativa:

\[
V(M)=\frac{\Delta z\,\Delta y\,\Delta x}{1000}\sum_{z,y,x}M[z,y,x]\quad[\mathrm{cm}^3],
\qquad A_z(M)=\Delta y\,\Delta x\sum_{y,x}M[z,y,x]\quad[\mathrm{mm}^2].
\]

Por ejemplo, **sintético, no resultado de paciente**: con espaciado \((2.5,0.78125,0.78125)\), 10 000 vóxeles ocupan 15,2588 cm³; 100 píxeles en un corte ocupan 61,0352 mm². Esta fórmula mide la máscara, no confirma su correspondencia con la lesión. Comparar \(V(M_1)\) con \(V(M_0)\) aquí compara métodos sobre la misma TC POST; no mide respuesta al tratamiento.

Para un corte ocupado, \(c_z=(\bar y_z,\bar x_z)\) es el promedio de los índices de sus píxeles. El salto mostrado es

\[
J_z=\sqrt{[\Delta y(\bar y_z-\bar y_{z-1})]^2+[\Delta x(\bar x_z-\bar x_{z-1})]^2}\quad[\mathrm{mm}].
\]

Es desplazamiento **en el plano**, no distancia 3D entre cortes: no suma \(\Delta z\). Si alguno de los dos cortes está vacío, el valor no está definido (`null`), no es cero. La correspondencia entre índices es `app_slice = z + 1`.

Los añadidos son \(N_+=|M_1\setminus M_0|\), los retirados \(N_-=|M_0\setminus M_1|\); el total cambiado es \(N_++N_-\). El «núcleo de 2 mm» es \(C_0=\{p:M_0(p)=1,\ d(p,M_0^c)>2\ \mathrm{mm}\}\), con distancia euclídea entre **centros** de vóxeles y espaciado anisotrópico. Se cuentan \(|C_0\cap M_1|\) y \(|C_0\setminus M_1|\); no se congela ese núcleo durante nnInteractive. Si el baseline toca el límite nativo, este descriptor no se calcula porque falta contexto exterior conocido.

Finalmente, \(G_z=|M[z-1]\cap M[z+1]\cap\neg M[z]|\) cuenta posiciones XY presentes en ambos vecinos pero ausentes en el corte central. En app55, \(z=54\). **No es una función objetivo ni un número de errores**: la geometría real puede cambiar entre cortes, y reducir \(G_z\) rellenando todo automáticamente podría añadir tejido ajeno al objetivo. Tampoco las componentes pequeñas son errores demostrados. Estos descriptores guían la inspección y una futura referencia independiente.

## Repetir el experimento en Docker

El [servicio de investigación](../../experiments/compose.neural.yaml) se combina con `compose.yaml`; no modifica el servicio `app`. La receta opcional fija las versiones del entorno y el archivo de código por SHA-256. **La imagen se construyó correctamente**: `pip check` no encontró dependencias rotas y los 25 archivos Python de nnInteractive coincidieron con el árbol fijado. La verificación de los cinco archivos del modelo pasó desde el nuevo contenedor, sin red. Las dos corridas iniciales descritas abajo usaron el entorno Linux aislado existente; la reproducción con esta imagen se registra por separado.

Desde la carpeta `hcc-tace-lab`, declarar todas las rutas antes de invocar Compose, porque interpola el archivo completo incluso al preparar pesos:

```sh
export HCC_UID="$(id -u)"
export HCC_GID="$(id -g)"
export HCC_DATA_DIR="$PWD/data"
export HCC_NEURAL_MODEL_ROOT="$PWD/../tmp/post-improvement/nninteractive/model"
export HCC_NEURAL_BASELINE="$PWD/../output/post-boundary/run-20261113T211925-17cd15d6"
export HCC_NEURAL_OUTPUT="$PWD/../output/post-neural-refinement"
mkdir -p "$HCC_NEURAL_MODEL_ROOT" "$HCC_NEURAL_OUTPUT"

docker compose -f compose.yaml -f experiments/compose.neural.yaml \
  --profile research build neural-research
```

`HCC_NEURAL_MODEL_ROOT` es el **directorio padre** de `nnInteractive_v1.0`, no el checkpoint ni esa subcarpeta. La ruta indicada reutiliza los pesos locales comprobados. Para una instalación nueva puede apuntar a otra carpeta fuera del código o a `data/models/nninteractive`, igualmente excluida de Git y del contexto de imagen. La siguiente operación descarga únicamente cuando falta el directorio del modelo y valida revisión, cinco archivos, tamaños y hashes; un directorio existente incompatible se rechaza, no se sobrescribe:

```sh
docker compose -f compose.yaml -f experiments/compose.neural.yaml \
  --profile research-setup run --rm --no-deps neural-assets
```

Los avisos de nnU-Net sobre `nnUNet_raw`, `nnUNet_preprocessed` y `nnUNet_results` aparecieron también en las ejecuciones terminadas. Esta ruta carga el modelo desde `--model-dir`; no inicia entrenamiento ni requiere asignar carpetas ficticias a esas variables.

`neural-assets` tiene red para esa descarga, pero no monta TC, baseline ni anotaciones. Para comprobar los pesos existentes sin descargar ni usar red, y después ejecutar HCC_006:

```sh
docker compose -f compose.yaml -f experiments/compose.neural.yaml \
  --profile research run --rm --no-deps neural-research \
  python -m experiments.prepare_nninteractive verify \
  --destination /models/nnInteractive_v1.0

docker compose -f compose.yaml -f experiments/compose.neural.yaml \
  --profile research run --rm --no-deps neural-research
```

El servicio de inferencia tiene red desactivada, límite de dos CPU y 7 GiB. Monta `/data`, `/models` y `/baseline` solo para lectura y `/research-output` para las salidas. No monta `/annotations`. Cada ejecución crea un nuevo `run-…`; no reescribe el baseline ni una corrida previa. Para HCC_002 se cambia solo la entrada congelada y se repite la última orden, **cuando haya terminado la corrida anterior**:

```sh
export HCC_NEURAL_BASELINE="$PWD/../output/post-boundary/run-20261113T212052-93e1217e"
docker compose -f compose.yaml -f experiments/compose.neural.yaml \
  --profile research run --rm --no-deps neural-research
```

Para reconstruir las figuras de una corrida terminada, sustituir `RUN_ID` por su carpeta real y elegir un destino nuevo. Este paso relee y verifica TC y artefactos; no ejecuta el modelo:

```sh
docker compose -f compose.yaml -f experiments/compose.neural.yaml \
  --profile research run --rm --no-deps neural-research \
  python -m experiments.plot_neural_refinement \
  --data /data --run /research-output/RUN_ID \
  --output /research-output/figures-RUN_ID
```

El punto de entrada es [post_neural_refinement.py](../../experiments/post_neural_refinement.py); el adaptador y sus guardas están en [nninteractive_refine.py](../../experiments/nninteractive_refine.py), y la descarga verificable en [prepare_nninteractive.py](../../experiments/prepare_nninteractive.py). Manifiestos, máscaras y figuras permanecen locales y fuera de Git. No basta reutilizar estos comandos sin las mismas fuentes, artefacto, configuración y versiones: los manifiestos permiten comparar esas condiciones.

## Comparaciones fijadas antes de ver el resultado

1. **Identidad e integridad.** Verificar retícula, SOP y SHA-256 de TC, baseline, código y pesos antes y después. La salida debe ser binaria y tener exactamente forma y correspondencia física nativas. Registrar foreground en padding y límites de adquisición; cualquier incompatibilidad es un fallo, no un hallazgo anatómico.
2. **Toda la estructura.** Revisar cada corte ocupado por cualquiera de las dos máscaras y sus vecinos, además de vistas coronal y sagital. Incluir extremos nuevos: no restringir la inspección a los cortes iniciales ni ocultar prolongaciones fuera del campo de Axivol.
3. **Cambios descriptivos.** Recuentos, volumen POST de cada salida, añadidos/retirados, extensión z, componentes 3D y sus tamaños, áreas por corte, huecos y desplazamientos del centroide. Los cambios de volumen son entre métodos sobre la misma TC; no son respuesta al tratamiento.
4. **Problema concreto y efectos secundarios.** Revisar app54→55 y 55→56 en HCC_006, su extensión gris en 56–57, las islas de 59–60 y el resto del objetivo. Menores saltos no bastan: una contracción excesiva también puede reducirlos. Examinar pérdida de regiones, expansión a estructuras vecinas, nueva fragmentación o diferencias dependientes de la ventana de visualización, sin identificar tejidos por su brillo solamente.
5. **Control y coste.** Aplicar el mismo procedimiento a HCC_002, sin ajustar parámetros después de mirar HCC_006. Registrar duración, pico de memoria, errores y extensión terminal. No elegir una variante diferente para cada paciente o corte.

**Criterio de decisión:** solo considerar una candidata experimental si la ejecución es íntegra y la revisión completa muestra una mejora defendible del problema sin regresiones materiales en otras regiones o en el caso de control. No se obliga a que toda métrica descriptiva disminuya. Si la anatomía permanece ambigua, conservar ambos resultados y mantener el default. No llamar falsos positivos, falsos negativos, Dice de exactitud o HD95 anatómico a diferencias entre estas dos predicciones. La [referencia independiente pendiente](annotation-kit.md) permitirá después evaluar esas cuestiones; no impide medir hoy estabilidad, cambios y recursos.

## Alternativa investigada, todavía no ejecutada

**MedSAM original ViT-B**, sin memoria entre cortes, permitiría una comparación distinta: cajas derivadas automáticamente de la máscara POST baseline, imagen POST actual por corte y pesos nuevos. Es un refinador 2D condicionado, no un segmentador volumétrico ni una referencia independiente; su ámbito y los cortes sin caja deberían declararse antes de ejecutarlo.

El [artículo primario](https://doi.org/10.1038/s41467-024-44824-z) fue verificado en Crossref y describe ViT-B entrenado con pares de imagen y máscara de diversas modalidades. Su preprocesado CT incluye W400/L40 para tejidos blandos. El [código fijado](https://github.com/bowang-lab/MedSAM/tree/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488) tiene licencia Apache-2.0; la inferencia usa 1024 × 1024, normalización [0,1] y decoder con caja. **No usa automáticamente la misma normalización ImageNet de MedSAM2.** El coste CPU de esta ruta todavía no se ha medido en el Mac.

La [carpeta de pesos enlazada por los autores](https://drive.google.com/drive/folders/1ETWmi4AiniJeWOt6HAsYgTjYv_fkgzoN) contiene `medsam_vit_b.pth`, aproximadamente 357,7 MB, ID `1UAmWL88roYR7wKlnApw5Bcuzf2iQgk6_`. Su [readme específico](https://drive.google.com/file/d/11ieGqh70aX7mCj1nHTGozEaP7ff61ejK/view) limita los modelos a investigación: no se transfiere la licencia Apache del código a los pesos. Todavía no se descargó ni calculó un hash local de este archivo. La conversión HF `wanglab/medsam-vit-base`, de mayo de 2023, no se sustituye sin comprobar equivalencia con el checkpoint original posterior. Tampoco está establecida la independencia respecto a HCC-TACE-Seg.

No se repiten como nueva solución el nnInteractive de una sola caja ni el comparador TotalSegmentator `liver_lesions`: ya se ejecutaron y sus limitaciones se conservan en [modelos abiertos POST-TACE](post-tace-models.md). Tampoco basta nombrar nnU-Net sin un checkpoint de lesiones, corpus y licencia concretos.

## Resultados

### HCC_006: cambio parcial, no solución completa

Ejecución local `output/post-neural-refinement/run-20261114T004808-d6970005`. El manifiesto indica `completed`, integridad de fuentes verificada, ningún posprocesado tras nnInteractive y ninguna escritura en el almacén de candidatas. **El baseline se copió exactamente del archivo congelado y no se regeneró**: `baseline_saved_exact=true`, `baseline_regenerated=false`. Por tanto, no se llama replay de inferencia a esta comprobación.

Se comprobaron de nuevo TC actuales, SOP ordenados, geometría, máscara binaria nativa, ausencia de foreground en padding y SHA-256 de todos los artefactos. Las 103 filas descriptivas se recalcularon y coincidieron exactamente con `slices.json`; también coincidieron los hashes del paquete visual. El baseline NPZ conserva SHA-256 `511fcc15336070f0aae794e418e97edcea5af7e5fca0188b60e2217c813bf940`. Los cinco archivos del modelo y los 25 módulos Python del paquete instalado quedaron identificados por hash; el árbol Python tiene SHA-256 `04acbc524c2639c944fdda762c7a8b6f11288212a9ccca7381f7692912557e98`.

La copia histórica local `output/post-neural-refinement/source-at-execution/index.json` relaciona el código recuperado con los hashes de cada manifiesto. Para HCC_006 coinciden 34 archivos inventariados, incluidos runner y adaptador. El plotter `plot_neural_refinement.py` recibió después una guarda de cobertura y ya no coincide con su hash inicial; no participa en la inferencia. El inventario también incluye módulos no ejecutados: no constituye una traza de llamadas. Una reproducción con un adaptador o una imagen Docker actualizados se registrará por separado, sin reescribir esta corrida ni afirmar que todo el código posterior es idéntico.

| Descriptor sobre toda la máscara HCC_006 | Baseline MedSAM2 + 2 mm | nnInteractive 3D |
| --- | ---: | ---: |
| Vóxeles | 11 355 | 10 984 |
| Volumen de esa máscara POST | 17,3264 cm³ | 16,7603 cm³ |
| Vóxeles añadidos / retirados frente al baseline | — | 189 / 560 |
| Componentes 3D de conectividad 6, tamaños | 11 342; 10; 2; 1 | 10 981; 1; 1; 1 |
| Foreground fuera de la componente mayor | 13 | 3 |
| Cortes ocupados, índices de array inclusivos | 49–64 | 49–64 |
| Cortes ocupados, numeración de la app | 50–65 | 50–65 |

Los 749 cambios afectan los 16 cortes ocupados; no son una corrección limitada a 55–56. Ningún corte ocupado se añade o desaparece. No hay foreground en padding ni fuera de la ROI histórica de localización, y la salida no toca límites de adquisición. El modelo eliminó las tres componentes secundarias originales, pero produjo tres componentes de un vóxel en otras posiciones: conservar cuatro componentes no significa conservarlas. No se aplicó una operación posterior para quitarlas.

| Plano y transición de interés | Antes | Después |
| --- | ---: | ---: |
| App54, `z=53`: píxeles | 635 | 623 |
| App55, `z=54`: píxeles | 462 | 492 |
| App56, `z=55`: píxeles | 709 | 709 |
| App57, `z=56`: píxeles | 901 | 881 |
| Centroide en plano, app54 → 55 | 5,237 mm | 3,681 mm |
| Centroide en plano, app55 → 56 | 6,958 mm | 5,882 mm |
| Píxeles presentes en app54 y 56 pero ausentes en 55 | 97 | 54 |

En las imágenes axiales W400/L40, app55 gana una pequeña extensión inferior. La extensión gris de app56–57 permanece considerablemente mayor; no se resolvió de forma convincente la transición completa. La revisión de las cinco páginas del atlas también muestra contracción en ambos extremos: app50 (`z=49`) pasa de 279 a 219 píxeles y app65 (`z=64`), de 288 a 196. Las vistas ortogonales muestran ajustes periféricos, sin establecer dónde debería terminar anatómicamente el objetivo. Los saltos y el conjunto de discrepancia disminuyen, pero son descriptores de continuidad entre predicciones; no prueban que los píxeles añadidos o retirados correspondan a tejido correcto.

**Precaución sobre el interior:** 25 de los 7 607 vóxeles del baseline situados a más de 2 mm de cualquier centro de fondo se retiraron. Se conservaron 7 582. Ese «núcleo» es una definición geométrica del modelo anterior, no una referencia de lesión; su retirada no demuestra error. Sí muestra que este método neuronal **no mantiene congelado el interior** como hacía el graph cut de banda, y que no sería correcto describir todos sus cambios como simples retoques de borde.

La envolvente reportada de escritura es `[[0,238,99],[103,430,291]]`, en ZYX y con extremos superiores exclusivos. Incluye todos los cortes, pero no certifica que se haya reevaluado cada vóxel interior. Fuera de ella la máscara permanece igual al baseline; había cero foreground inicial allí. La entrada conserva toda la TC nativa y la salida completa, sin recorte final a esa envolvente.

Carga del modelo: 5,480 s. Preprocesamiento e inferencia: 237,412 s. Runner completo: 245,069 s. Pico RSS: 6 467,254 MiB, aproximadamente 6,32 GiB, en Linux aarch64 y Python 3.12.16 con dos hilos de Torch. No se comparan estos tiempos como un benchmark equiparado con MedSAM2. El coste es material para una interacción frecuente.

Paquete visual local: `output/post-neural-refinement/figures-HCC006-nninteractive-final`. Incluye focos axiales W400/L40 y W800/L200, vistas ortogonales, cinco páginas de atlas, PDF y tablas de los 103 cortes. Los volúmenes e imágenes se conservan fuera de Git. Los resultados corresponden exclusivamente al refinamiento de etiqueta inicial descrito, no al antiguo ensayo con caja.

### HCC_002: cambios extensos y efectos en los extremos

Ejecución local `output/post-neural-refinement/run-20261114T005432-9be97d56`. Se usó el mismo modelo y configuración, sin ajuste posterior a HCC_006. La TC es POST adquisición 1; la procedencia de la localización histórica es PRE adquisición 1, `Mass` segmento 2 y `Liver` segmento 1. El baseline quedó copiado exactamente, SHA-256 `6fbe0b8318d6820707c5f59169a9f3a8e729a0121656a80dff09fcd6e07d04a6`, sin regenerarlo ni recalcular registro.

La relectura independiente de TC, SOP, geometría y artefactos pasó; las 97 filas descriptivas recalculadas coinciden exactamente con las del paquete visual. También coinciden sus hashes. No hay foreground en padding, fuera de la ROI histórica o en límites nativos. Estos son controles técnicos, no una comprobación de correspondencia anatómica.

| Descriptor sobre toda la máscara HCC_002 | Baseline MedSAM2 + 2 mm | nnInteractive 3D |
| --- | ---: | ---: |
| Vóxeles | 413 118 | 404 848 |
| Volumen de esa máscara POST | 630,3680 cm³ | 617,7490 cm³ |
| Vóxeles añadidos / retirados | — | 14 616 / 22 886 |
| Total cambiado respecto al baseline | — | 37 502; 9,078 % |
| Componentes 3D de conectividad 6, tamaños | 413 112; 4; 2 | 404 848 |
| Cortes ocupados, índices de array inclusivos | 46–86 | 47–85 |
| Cortes ocupados, numeración de la app | 47–87 | 48–86 |
| Vóxeles del núcleo inicial >2 mm retenidos | 378 032 | 366 922 |

Los 41 cortes inicialmente ocupados cambian; los dos extremos app47 (`z=46`) y app87 (`z=86`) quedan vacíos. No aparecen nuevos cortes ocupados. Se retiran 11 110 vóxeles del núcleo geométrico inicial: nuevamente, el refinamiento no se limita a una banda de 2 mm ni preserva todo el interior. Esto no demuestra que esos vóxeles debieran conservarse anatómicamente; sí obliga a revisar más que el contorno externo.

| Corte nativo / app | Píxeles antes → después | Componentes 2D antes → después |
| --- | ---: | ---: |
| `z=46` / app47 | 1 145 → 0 | 3 → 0 |
| `z=47` / app48 | 3 811 → 1 590 | 1 → 6 |
| `z=48` / app49 | 5 783 → 3 802 | 1 → 4 |
| `z=84` / app85 | 5 505 → 3 608 | 1 → 1 |
| `z=85` / app86 | 1 557 → 447 | 1 → 8 |
| `z=86` / app87 | 1 087 → 0 | 2 → 0 |

Las componentes 2D usan conectividad 4; las 3D, conectividad 6. **Una sola componente 3D no implica contornos más continuos en cada corte.** En las páginas 1 y 9 del atlas se ven las pérdidas terminales y nuevas separaciones internas; las vistas ortogonales muestran una estructura central todavía amplia con modificaciones periféricas. No se puede decidir solo por esa apariencia si cada retirada es correcta, pero el resultado no demuestra una mejora uniforme de continuidad. Tampoco debe llamarse «limpieza de falsos positivos» a estas retiradas.

La envolvente de escritura reportada es `[[0,205,0],[97,420,300]]` ZYX, extremos superiores exclusivos. No es la envolvente de la máscara ni un mapa exacto de inferencia; por ello que llegue a `x=0` no significa que la predicción toque ese borde. Fuera de ella se conservó el baseline y había cero foreground previo. Se mantuvieron todos los cortes nativos.

Carga: 5,670 s. Preprocesamiento e inferencia: 1 081,892 s; runner completo: 1 089,941 s, aproximadamente **18,17 minutos**. Pico RSS: 6 718,625 MiB, aproximadamente 6,56 GiB. El log registra AutoZoom 1,1510417 y tres cajas de refinamiento, frente a un objetivo considerablemente menor en HCC_006. El coste no puede resumirse como «unos cuatro minutos por caso» basándose solo en HCC_006.

Paquete local: `output/post-neural-refinement/figures-HCC002-nninteractive-final`, con diez páginas de atlas, focos en dos ventanas, vistas ortogonales y las 97 filas CSV/JSON. No se copian las imágenes médicas al repositorio.

### Decisión y significado para la tesis

**Se conserva el método de la aplicación y no se reemplaza su candidata activa.** El resultado HCC_006 mejora modestamente algunos descriptores de la transición 55–56, pero no la resuelve de forma convincente; el control cambia sustancialmente ambos extremos, introduce separaciones en cortes previamente conectados y cuesta unos 18 minutos. No existe evidencia suficiente para recomendar la sustitución automática. No se elige una salida distinta para cada paciente ni se ocultan estos efectos al reportar el resultado parcial de HCC_006.

El experimento sí establece una ruta reproducible para comparar refinamiento volumétrico aprendido con el método actual, sin dibujar nuevos prompts. En un informe de tesis debe describirse como **comparación de desarrollo de dos predicciones dependientes**, con hipótesis y resultados parcialmente negativos; dos casos no permiten estimar generalización. La pendiente referencia experta independiente es la que permitirá decidir si cambios de volumen, núcleo o contorno son aciertos o errores. Un lector que solo corrija estas predicciones produciría una referencia influida por ellas; véase el [kit de anotación](annotation-kit.md).

No se buscarán automáticamente otros parámetros para rellenar el corte 55. MedSAM ViT-B sigue siendo una alternativa documentada y no ejecutada, con una hipótesis distinta y un coste todavía sin medir; no se presenta como solución probada.

Se aplicó `model-selection` para escoger el artefacto y separar licencia, procedencia, compatibilidad y alcance. La ejecución local ya autorizada forma parte del experimento; los [créditos procedimentales](README.md#herramientas-de-apoyo-y-crédito) no constituyen evidencia de exactitud del método.


## Verificación del código y de las figuras

La revisión del código detectó un caso límite de normalización: un bloque constante rodeado de ceros no es una imagen globalmente constante, pero la desviación estándar de su región de normalización puede ser cero. El adaptador ahora rechaza esa entrada tras la conversión a float32. Además, espera explícitamente el preprocesamiento asíncrono oficial y comprueba su tensor normalizado real, plano por plano, antes de inferir: forma nativa y valores finitos. No reproduce las sumas internas del modelo ni intenta reparar NaN/Inf mediante otra imagen. La llamada privada usada para esperar se encuentra en el commit de código verificado; debe revisarse al actualizar nnInteractive.

El lector de figuras también verifica la envolvente de escritura y la igualdad real de ambas máscaras fuera de ella; no se limita a repetir la leyenda del manifiesto. La exportación conserva contornos de píxeles nativos y proporción física. Se revisaron 21 PNG entre ambos casos, ocho PDF con fuentes TrueType y 33 salidas cuyos hashes coinciden. El informe local es `output/post-neural-refinement/figure-qa-final.json`.

Validación final en la imagen `app` reconstruida, con `HCC_PILOT_TEST=1`: **291 pruebas en 141,469 s; 289 aprobadas y dos omitidas** por dependencias opcionales de figuras. Las dos omitidas son las exportaciones del comparador previo y del nuevo: se ejecutaron en el entorno de figuras, donde pasaron los **19 tests** correspondientes (10 anteriores y nueve nuevos). Los tests cubren integridad de fuentes, geometría y padding, no mutación, máscara vacía sin sustitución, cobertura de escritura, pesos alterados, normalización inválida y separación de las anotaciones de la aplicación. No constituyen validación clínica.

La aplicación respondió `health=ok` y conservó la candidata HCC_006 `25b9f264bf5b40dfa2dfc54cb4b30f6b`, estado `unreviewed`, con SHA-256 de máscara `511fcc15336070f0aae794e418e97edcea5af7e5fca0188b60e2217c813bf940`. No hubo cambios de interfaz; esta revisión no atribuye nuevas pruebas de navegador a una interfaz que no se modificó.


### Reproducción con la receta Docker publicada

Después de corregir la validación de normalización se volvió a ejecutar HCC_006, sin cambios de parámetros, con `experiments/compose.neural.yaml`: corrida `run-20261114T012000-b6cd5f89`, imagen local `sha256:d458c69fdb9fde2c7994a7d5d081732ed42ec6346732b1803aa78a2037dcd032`. El contenedor terminó con código 0, sin OOM, sin red, con dos CPU y límite de memoria/swap de 7 GiB. Datos, baseline y modelos estaban en solo lectura; no montó anotaciones.

La salida contiene **10 984 vóxeles y cero diferencias** respecto a la primera ejecución de HCC_006. Coinciden también los bytes del NPZ final: SHA-256 `c9507a1e589e8235541037579abaca3b960a25f295f2053f78308d8af38b6e3a`. Se verificaron iguales fuentes, baseline y procedencia del modelo; el lector actualizado volvió a comprobar TC nativa, hashes y cobertura. El registro local `output/post-neural-refinement/docker-reproduction.json` conserva la comparación, y cada corrida tiene su `container.json`.

El runner tardó 271,714 s, de los cuales 264,921 s corresponden a preprocesamiento e inferencia; pico RSS 6 549,875 MiB. Esta reproducción en el mismo equipo comprueba la ruta publicada y que el control de normalización no alteró esta salida. No demuestra igualdad entre otros equipos ni mejora anatómica. HCC_002 no se repitió con la imagen nueva: sus resultados corresponden al entorno aislado inicial. No cambió la decisión de mantener el protocolo 2.1.0 en la app.
