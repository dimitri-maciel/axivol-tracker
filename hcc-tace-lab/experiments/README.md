# Experimentos POST reproducibles

Este módulo investiga por qué cambia una predicción cuando cambian el contexto, la ventana de imagen o la localización. Lee las TC y el SEG originales mediante el catálogo de Axivol. **No necesita una candidata previa, un identificador de anotación ni el directorio de cuarentena. No guarda resultados en la aplicación.**

La máscara PRE `Mass` proporciona centro y caja. La variante hepática usa `Liver ∪ Mass` PRE únicamente para refinar el registro. Todas las máscaras de salida se calculan con intensidades POST. No hay una referencia POST independiente: los números describen ejecución y sensibilidad, no exactitud anatómica ni respuesta al tratamiento.

**Estado del código, 13 de noviembre de 2026:** la implementación `2.1.0`, protocolo `liver_context_medsam2_boundary`, añade refinamiento 3D con logits en una banda fija de 2 mm. **Desplegada y comprobada:** 189/189 pruebas de backend, generación real de HCC_006 y siete grupos de comprobaciones de navegador en HCC_002/HCC_006. Se mantiene pendiente la validación anatómica independiente. `post_boundary` estudia ese cambio; `post_tracking` conserva las ablaciones de contexto sin el nuevo ajuste de borde. Véanse la [arquitectura](ARCHITECTURE.md) y las [ecuaciones del refinamiento POST](../docs/research/post-boundary-refinement.md).

## Reproducir las ablaciones de contexto

Requisitos: descargar las series CT y SEG del caso con el flujo de ingestión del proyecto, construir la imagen `app` y descargar el checkpoint fijado según el README principal. No se descargan modelos durante este experimento. Se verifican SHA-256 del checkpoint y YAML antes de inferir. Las dependencias son las de la imagen; no hay un entorno adicional para ejecutar este runner.

Desde `hcc-tace-lab`:

```bash
mkdir -p ../output/post-experiments
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/../output/post-experiments:/experiment-output" \
  --entrypoint python app -m experiments.post_tracking \
  --data /data --output /experiment-output \
  --manifest /app/experiments/manifest.example.json --case HCC_006 \
  --variants full local160 local240 local160wide local160liver
```

El manifiesto contiene identificadores técnicos públicos para HCC_002 y HCC_006. Las adquisiciones se seleccionan por `AcquisitionNumber`, no por un índice que pueda cambiar al reindexar. El runner rechaza series de otro caso/etapa, adquisiciones ambiguas y segmentos cuyo número no corresponda a `Mass` o `Liver`. La asignación PRE/POST utiliza el orden de estudios del catálogo; no verifica equivalencia de fase de contraste.

Para el control HCC_002 se usa el mismo comando con `--case HCC_002 --variants full local160`. También se pueden reemplazar `--manifest` por los argumentos explícitos `--pre-series-uid`, `--post-series-uid`, `--pre-acquisition`, `--post-acquisition`, `--mass-segment` y `--liver-segment`. Las dos modalidades no se mezclan.

Se ejecuta un predictor CPU de dos hilos y una variante a la vez. **No lanzar simultáneamente otra inferencia pesada.** Los pilotos tardaron aproximadamente 51–56 segundos por adquisición y variante, con pico de proceso cercano a 1 GiB; no es una garantía para otras imágenes o equipos. La inferencia admite hasta 256 cortes y 64 millones de vóxeles; el límite temporal de 300 segundos se comprueba entre cortes y no interrumpe un kernel bloqueado.

## Refinamiento POST: bandas de 1/2/3 mm

Con los mismos requisitos y la imagen reconstruida con el código actual:

```bash
mkdir -p ../output/post-boundary
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/../output/post-boundary:/experiment-output" \
  --entrypoint python app -m experiments.post_boundary \
  --data /data --output /experiment-output \
  --manifest /app/experiments/manifest.example.json --case HCC_006
```

Cambiar solamente `--case HCC_002` permite repetir el procedimiento en el otro caso de desarrollo. El runner realiza una inferencia con registro hepático y contexto XY de 160 mm. Conserva los logits originales de la caja en el corte ancla, obtiene raw y aplica **por separado** bandas de 1, 2 y 3 mm sobre los mismos arrays. La banda principal es **2 mm**, con `smoothness_mm=0.5` y `edge_sigma_hu=20` fijos. No selecciona la variante con volumen o aspecto preferidos; si falla la principal, no la sustituye por otra.

Cada carpeta nueva contiene `raw.npz`, `refined-1mm.npz`, `refined-2mm.npz` y `refined-3mm.npz` con máscaras nativas; `logits-field.npz` conserva el campo `[z,y,x]`, offsets y geometría. `comparison.json` registra añadidos/retirados, componentes, caja, padding, ROI y contactos de campo. `manifest.json` fija selección, orden SOP, hashes CT/SEG antes y después, checkpoint, configuración, código y versiones. Los artefactos fallidos se describen sin inventar una máscara.

`application_checks` indica si una variante activaría un rechazo espacial de la app, incluido contexto insuficiente cuando la banda alcanza un límite XY artificial. No es validación anatómica. Las energías antes/después solo se comparan **dentro de una misma banda**, porque nodos y constantes cambian entre bandas. Los logits no son probabilidades clínicas calibradas y el cambio raw–refinada no es error frente a una referencia. Para aislar este cambio se comparan raw y refinada de **la misma ejecución**, no con un conteo histórico cuyo registro fue recalculado.

El plotter opcional requiere las dependencias de lectura del proyecto y Matplotlib, fijado en `requirements-figures.txt`, en un entorno de figuras separado de la app. Con ese entorno ya preparado y rutas reales accesibles:

```bash
python -m experiments.plot_boundary \
  --data /data --run /run-folder \
  --output /research-output/HCC006-boundary
```

Genera PNG, PDF y JSON sin sobrescribir salidas, tras verificar fuentes y hashes de raw/refinada de 2 mm. Compara TC, raw, refinada y diferencias en planos axiales, coronal y sagital, con proporción física y ventana fija de nivel 40/ancho 400 HU. No produce una referencia experta ni guarda candidatas en la app.

## Cortes 55–56: condicionamiento, seguimiento y entrada

`post_prompt_conditioning` compara tres hipótesis por separado con una localización ya congelada. Primero exige reproducir **exactamente** raw y refinada de 2 mm del baseline. Después cambia una política declarada, sin recalcular registro ni alterar las TC o candidatas de la app. La [investigación completa](../docs/research/post-prompt-conditioning.md) documenta mecanismos, resultados y límites.

Con la imagen construida desde el código actual:

```bash
mkdir -p ../output/post-prompt-conditioning
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/../output/post-boundary:/frozen-boundary:ro" \
  --volume "$PWD/../output/post-prompt-conditioning:/experiment-output" \
  --entrypoint python app -m experiments.post_prompt_conditioning \
  --data /data \
  --baseline /frozen-boundary/run-20261113T211925-17cd15d6 \
  --output /experiment-output --after box
```

Los valores de `--after` son `box`, `single_tracking` y `upstream_input`. Se ejecutan en procesos separados y secuenciales. `--probe` registra las alternativas del decoder sin modificar su selección. Para HCC_002 se usa el baseline `run-20261113T212052-93e1217e`. Esos identificadores corresponden a los ensayos locales documentados; otra instalación debe producir y usar su propio baseline con `post_boundary`.

El plotter verifica los hashes de la TC y de los artefactos antes de generar figuras. En el entorno de figuras con `requirements-figures.txt`:

```bash
python -m experiments.plot_prompt_conditioning \
  --data /data --run /experiment-output/run-identificador \
  --output /research-output/comparison
```

Produce comparaciones axiales de los cortes 54–57, planos coronal y sagital, atlas de toda la extensión de ambas máscaras con vecinos, y descriptores de **todos** los cortes adquiridos en CSV/JSON. Las ventanas de visualización W400/L40 y W800/L200 se mantienen iguales entre brazos y no cambian la ventana usada para inferir. Los contornos corresponden a los píxeles originales, sin suavizado de presentación. Antes/después significa versiones del algoritmo sobre la misma TC POST, nunca evolución del tumor ni precisión frente a una referencia.

## Variantes declaradas

| Variante | Registro | Contexto XY de entrada | Nivel/ancho HU |
|---|---|---|---|
| `full` | Global rígido | Campo completo | 40/400 |
| `local160` | Global rígido | Mínimo 160 mm | 40/400 |
| `local240` | Global rígido | Mínimo 240 mm | 40/400 |
| `local160wide` | Global rígido | Mínimo 160 mm | 200/800 |
| `local160liver` | Global, seguido de refinamiento con `Liver ∪ Mass` PRE | Mínimo 160 mm | 40/400 |

Son hipótesis de ingeniería fijadas para esta comparación, no ajustes clínicos publicados ni parámetros elegidos para obtener cierto volumen.

Para el contexto local, el lado físico es `max(mínimo, 2 × mayor lado físico de la caja localizada)`. Se convierte a píxeles por separado con `dy` y `dx`; por ello una imagen anisotrópica puede necesitar distintos tamaños de píxel para igual tamaño físico. El campo se desplaza hacia dentro del FOV cuando es necesario y debe contener toda la caja. **Todos los cortes z se procesan**, también los que quedan fuera de la extensión PRE.

Cada plano de contexto se convierte en entrada 512 × 512 mediante interpolación bilineal si hace falta. Los logits se devuelven al tamaño nativo del contexto y se umbralizan en cero. La máscara se inserta usando sus offsets en un volumen con la forma original POST. Fuera del campo XY de inferencia hay ceros de almacenamiento: **esa región no fue evaluada**, no se ha demostrado que sea fondo. El informe conserva el campo y los contactos de borde. Nunca se recorta una predicción ya calculada para hacerla pasar por el control de ROI.

La ventana transforma `u = clip((HU − (nivel − ancho/2))/ancho, 0, 1)`, repite tres canales y aplica la normalización ImageNet del predictor. El padding se representa como cero de entrada y se excluye de la salida. El valor de HU original no se cambia.

## Registro y procedencia

Se ejecuta **una vez** el registro global de `hcclab.registration` por invocación/caso. Fija PRE, mueve POST y devuelve una transformación física PRE → POST; no se invierte para localizar los puntos PRE.

El refinamiento hepático opcional parte de ese resultado y optimiza información mutua Mattes sobre `Liver ∪ Mass` PRE, con máscara corporal POST. Usa retícula auxiliar de 3 mm, 50 bins, muestreo 30 %, semilla 20261112, un hilo, pirámide 2/1, suavizado 1/0 mm, descenso de paso regular 1.0/0.02, relajación 0.5 y hasta 150 iteraciones por nivel. Los parámetros iniciales/finales, métrica y parada se guardan. Esta localización también queda **sin verificar**; la región `Liver` publicada puede excluir `Mass`.

El wrapper experimental delega en `hcclab.registration.refine_pre_to_post`. Ese helper recibe un contexto booleano PRE y una transformación Euler3D inicial explícita; devuelve solamente transformación y diagnóstico. Excluye padding, exige al menos 64 vóxeles de contexto en la retícula auxiliar y 32 puntos válidos de métrica, y rechaza transformaciones no rígidas o que excedan 20°/100 mm. El límite cooperativo es 30 segundos. Estos controles numéricos no comprueban correspondencia anatómica. Las 134/134 pruebas y revisiones UI HCC_002/HCC_006 documentadas al desplegar `2.0.0` son antecedentes históricos; no validan el nuevo refinamiento de `2.1.0`. Véase el [registro de arquitectura](ARCHITECTURE.md).

Los informes registran series/adquisiciones/segmentos explícitos, identidad y geometría de retículas, hashes CT ordenados y SEG, pesos, configuración, versiones, plataforma y hashes de los módulos de experimentación y aplicación. Se comprueban de nuevo las fuentes antes de escribir cada resultado. Los píxeles originales no se reescriben.

## Archivos de salida

Cada ejecución crea un directorio nuevo `run-fecha-id` fuera de los datos, `/annotations` y el árbol de código:

- `manifest.json`: entradas, versiones, hashes, registro, variantes y estado final.
- `<variante>.npz`: `mask` bool `[z,y,x]` y `prompt` bool `[y,x]`, ambos POST nativos. No se usa pickle.
- `<variante>.json`: parámetros, áreas/scores por corte, centroides, cobertura de ROI, contactos con el campo de inferencia y hash del NPZ. Los fallos también generan un informe.
- `prediction_consistency.json`: Dice/IoU **entre predicciones**, con `not_accuracy: true`. No son métricas contra verdad de referencia.

El guard de mayoría fuera de ROI de la aplicación se omite deliberadamente en estas ablaciones, para estudiar también los fallos. Cada informe lo declara y la máscara nunca se registra en `CandidateStore`. Pasar ese guard no valida anatomía. Ni los scores del tracker ni los vóxeles por encima de 240/600 HU identifican tumor viable o Lipiodol; los umbrales solo describen el efecto de las ventanas usadas.

Los NPZ, imágenes, CT y archivos derivados se conservan localmente. No agregarlos a Git ni al contexto de construcción. El repositorio contiene únicamente runner, manifiesto técnico, documentación y agregados numéricos de desarrollo.

## Curvas de área por corte

El plotter toma los informes de `post_tracking`, verifica procedencia y máscaras, y conserva unidades y cortes nativos. En el entorno de figuras ya preparado con las dependencias opcionales de `requirements-figures.txt`:

```bash
python -m experiments.plot_tracking \
  /path/run/local160liver.json --output /research-output/area
```

Se generan PNG, PDF, CSV y JSON con hashes. Para comparar variantes se añaden sus rutas JSON al mismo comando. Las curvas comparan predicciones, no error frente a una referencia POST; tampoco demuestran respuesta al tratamiento.

## Pruebas y alcance

```bash
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/tests:/app/tests:ro" \
  --entrypoint python app -m unittest discover -s tests -p test_post_experiments.py -v
```

Estas pruebas no cargan pesos. Comprueban espaciado anisotrópico, centros asimétricos, contención de caja, límites del FOV, exclusión de padding, colocación con offsets, no mutación, adquisición explícita y separación de fuentes/salidas. La suite completa del proyecto sigue siendo necesaria antes de integrar cambios de aplicación.

Para los contratos del runner de borde, usar el mismo comando con `-p test_post_boundary_experiment.py`. Comprueba una sola inferencia, offsets, consistencia raw/logits, copias independientes por banda, padding, contexto artificial y rechazo de fuentes modificadas. Las siete pruebas sintéticas de este runner pasaron. El cierre de `2.1.0` incluye 189/189 pruebas de backend, generación real HCC_006 y siete grupos de comprobaciones de navegador; consultar el registro de integración en la guía de refinamiento.

Los seis pilotos iniciales y sus fallos se documentan en [ABLATION.md](ABLATION.md). Como antecedente de `2.0.0`, tras extraer el runner se ejecutó desde CT/SEG original la variante `local160liver` de HCC_006: 11120 vóxeles, cortes 49–64, 0 % fuera de ROI y 50.83 s de inferencia. Esta reproducción única no repite todo el lote ni garantiza igualdad bit a bit. El registro recalculado cambia ligeramente respecto al piloto congelado, que produjo 11485 vóxeles. Ninguno de esos conteos incluye el refinamiento de borde de `2.1.0`.

## Fuentes y adaptación

El checkpoint y la inferencia CPU provienen de [MedSAM2, commit fijado](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py). Se mantienen caja → máscara inicial, reinicio antes de propagación inversa y normalización del predictor. El script original utiliza indicación RECIST POST y limita su extensión z; aquí no existe esa anotación, por lo que seguir toda la adquisición es una adaptación experimental. El contexto XY local también es una ablación propia, no una característica clínicamente validada del modelo.

Se importan helpers privados de la aplicación para reutilizar exactamente el cargador de pesos/configuración. Es una dependencia de investigación explícita; los hashes registran esos módulos y los tests deben revisarse si sus contratos cambian. El registro hepático y el campo XY se comparten con la app; `post_boundary` reutiliza además `post_model`, `post_boundary` y el solver de `refinement` de `2.1.0`. Los runners conservan variantes alternativas y resultados fallidos fuera del almacén de candidatas; su ejecución no modifica el protocolo activo de la app.

## Referencia POST independiente

La referencia debe delimitarla un especialista sin ver primero la predicción del modelo. Los comandos siguientes preparan archivos y comparan una referencia suministrada; no generan anotaciones expertas. HCC_006 continúa siendo un caso de desarrollo. Véanse la [guía para el lector](../docs/research/annotation-kit.md) y el [protocolo de validación](../docs/research/post-tace-validation.md).

### Preparar el caso

Reconstruir la imagen con `docker compose build app`. Desde `hcc-tace-lab`, descargar antes el caso y generar su candidata en la aplicación. Usar su identificador real en `AXIVOL_CANDIDATE_ID`; no escoger una máscara después de ver la referencia.

```bash
mkdir -p ../output/reference-review
AXIVOL_CANDIDATE_ID=identificador_de_la_candidata_guardada
docker compose run --rm --no-deps -T \
  --volume "$PWD/../output/reference-review:/reference-output" \
  --volume hcc-tace-lab_annotations:/annotations:ro \
  --entrypoint python app -m experiments.prepare_reference \
  --output /reference-output \
  --manifest /app/experiments/manifest.example.json \
  --case HCC_006 --candidate-id "$AXIVOL_CANDIDATE_ID"
```

El comando imprime una carpeta nueva `HCC_006-reference-…`. Contiene:

- `reader/` y `reader.zip`: TC POST completa, TC PRE y `Mass` original, identificación, formulario pendiente, instrucciones, ayudante Python y acceso `Open_HCC_in_Slicer.command` para macOS. No contienen máscara, caja, corte destacado ni estadísticas de la predicción POST.
- `manager/`: candidata congelada, soporte válido de CT, manifiesto de evaluación y copia de la identidad del paquete. No entregar al lector.
- `preparation.json`: controles técnicos de exportación y estado pendiente. La inexistencia de una referencia no se convierte en un resultado de exactitud.

La exportación relee cada NRRD y compara todos sus valores, forma, origen, espaciado y orientación. Comprueba los hashes de CT/SEG/candidata antes y después. No registra, interpola, recorta ni modifica las imágenes originales. Los archivos y el ZIP permanecen en `output/`, fuera de Git y del contexto Docker. Un ZIP sin predicción no prueba el cegamiento del lector: la exposición previa se declara separadamente.

El ayudante `open_in_slicer.py` requiere 3D Slicer y una escena vacía. No borra escenas ni genera un contorno. En macOS, `reader/Open_HCC_in_Slicer.command` abre una instancia nueva de `/Applications/Slicer.app`; se guarda el borrador con `save_reference()` en su consola Python. La alternativa multiplataforma usa `runpy`, `kit['open_case']()` y `kit['save_reference']()`, como indica la guía. Esta también incluye exportación manual. Conservar la nota de operaciones del lector junto con `export.json`.

### Prueba funcional de Slicer

El 12 de noviembre de 2026 se instaló y probó Slicer **5.12.4** en macOS. Se completaron tres ejecuciones gráficas con HCC_006: PRE adquisición 3, `Mass` segmento 2 y POST adquisición 1. La última registró seis comprobaciones satisfactorias en `output/slicer-qa/run-20261113T033001-ac89d200/validation.json`:

- Geometría nativa PRE y POST idéntica a los NRRD de origen.
- Solo entradas del lector cargadas, máscara PRE oculta y borrador POST vacío.
- Un trazo no vacío creado mediante eventos reales del efecto **Paint**.
- **Undo** recuperó el borrador vacío y **Redo** restauró exactamente el trazo.
- Exportación y reapertura conservaron píxeles binarios y geometría; las declaraciones del lector permanecieron `null`.
- Los archivos del lector conservaron sus bytes y no se cargó ni escribió una candidata.

La ejecución medida duró **6,14 s, sin incluir el arranque de Slicer**. Es tiempo de una comprobación funcional, no tiempo de anotación experta ni rendimiento anatómico. El trazo fue arbitrario: no se calculó Dice, HD95 ni otra métrica de exactitud de HCC_006.

Para repetir desde `hcc-tace-lab` en macOS, sustituir `PAQUETE` por la carpeta real. La salida QA debe quedar fuera de `reader/`:

```bash
AXIVOL_READER_DIR="$PWD/../output/reference-review/PAQUETE/reader"
AXIVOL_SLICER_QA_DIR="$PWD/../output/slicer-qa"
open -n -W -a /Applications/Slicer.app \
  --env "AXIVOL_READER_DIR=$AXIVOL_READER_DIR" \
  --env "AXIVOL_SLICER_QA_DIR=$AXIVOL_SLICER_QA_DIR" \
  --env AXIVOL_SLICER_QA_EXIT=1 \
  --args --no-splash --ignore-slicerrc \
  --python-script "$PWD/experiments/slicer_annotation_smoke.py"
```

El script crea una carpeta `run-…` nueva con comprobaciones, capturas y un retorno marcado como ensayo funcional. `AXIVOL_SLICER_QA_EXIT=1` cierra esa instancia de Slicer al terminar; `-W` espera su cierre. No se incorpora el trazo a `reader.zip`, al almacén de candidatas ni a una referencia del evaluador. Nadie debe completar declaraciones expertas para convertir esta prueba en una evaluación anatómica.

### Evaluar un retorno auténtico

Solo después de recibir `POST-label.nrrd` y `reader_form.json` completado por un lector cualificado. Sustituir `PAQUETE` y `RETORNO` por los nombres reales dentro de `output/reference-review`:

```bash
docker compose run --rm --no-deps -T \
  --volume "$PWD/../output/reference-review:/review" \
  --volume hcc-tace-lab_annotations:/annotations:ro \
  --entrypoint python app -m experiments.evaluate_reference \
  --manifest /review/PAQUETE/manager/evaluation_manifest.json \
  --candidate /review/PAQUETE/manager/candidate.nrrd \
  --reference /review/PAQUETE/reader/RETORNO/POST-label.nrrd \
  --reader-form /review/PAQUETE/reader/RETORNO/reader_form.json \
  --output /review/PAQUETE/comparison-reader-A.json
```

El evaluador rechaza formularios incompletos, exposición declarada a la predicción, referencia incierta, archivos sustituidos, píxeles fuera del soporte válido y retículas incompatibles. Los booleanos son declaraciones del lector, no verificación de credenciales; nunca rellenarlos ficticiamente para habilitar métricas. No acepta una máscara multicapa `.seg.nrrd`, una imagen con etiquetas distintas de 0/1 ni NRRD con datos externos.

El JSON conserva fuentes, parámetros de métricas, versiones y hashes. Dice mide superposición; HD95 usa el máximo de los percentiles 95 dirigidos de distancias físicas entre centros de vóxeles de borde 6-conexo, sin ponderación de área. El volumen usa la retícula POST. Con superficies vacías, HD95 es `null`; con ambas máscaras vacías, Dice también es `null`, y se informa concordancia de ausencia separadamente. No se genera respuesta terapéutica ni una afirmación de generalización.
