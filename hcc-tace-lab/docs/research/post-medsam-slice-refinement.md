# Refinamiento POST por corte con MedSAM original

**Estado: protocolo predeclarado y ejecutado en HCC_006 y HCC_002; variante de investigación, sin adopción.** MedSAM ViT-B refinó por separado los planos inicialmente ocupados de la candidata POST. HCC_006 muestra una reducción marcada del salto 55→56, acompañada de cambios importantes en otras zonas; el control HCC_002 también cambia ampliamente y presenta transiciones nuevas más abruptas. HCC_005 y el único reemplazo previsto, HCC_004, no pudieron producir un baseline compatible. El método de la aplicación no cambia.

## Hipótesis y alcance

La transición entre app55 y app56 de HCC_006 sigue siendo problemática después del [refinamiento nnInteractive](post-neural-refinement.md). La nueva hipótesis es que un modelo que vuelva a delimitar cada imagen axial a partir de una caja estable de contexto pueda modificar esa transición sin depender de la memoria de un seguimiento entre cortes.

Se elige **MedSAM original**, no MedSAM2 ni LiteMedSAM. Cada imagen se procesa por separado; la predicción del corte anterior no se entrega al siguiente. Esto no es un experimento que aísle exclusivamente la memoria: también cambian los pesos, la arquitectura, la resolución de entrada y el prompt. Un resultado distinto se atribuirá al enfoque completo, no a una causa única demostrada.

El objetivo es **refinar los planos donde la candidata POST ya existe**, no detectar de nuevo todas las lesiones ni decidir ausencia en todo el estudio. Solo se ejecuta la red en cortes con foreground en el baseline. Los demás conservan el baseline con estado explícito `inherited_baseline_not_reevaluated_not_confirmed_absence`. No se confunde esa herencia con una conclusión de ausencia. La retícula y el número total de cortes de la salida permanecen nativos.

Esta limitación es deliberada: MedSAM recibe una caja que señala un objeto; no es un detector de ausencia. Aplicar la misma caja indiscriminadamente en todos los cortes podría pedirle que segmente otras estructuras donde el objetivo ya no aparece. A cambio, **este protocolo no puede descubrir una extensión z que falte completamente en el baseline**, y sí puede eliminar alguno de sus cortes terminales. Ambos efectos se declararán.

## Fuentes, artefacto y licencias

El artículo de Ma et al., *Segment anything in medical images*, Nature Communications 15, 654 (2024), DOI [10.1038/s41467-024-44824-z](https://doi.org/10.1038/s41467-024-44824-z), se verificó en [Crossref](https://api.crossref.org/works/10.1038/s41467-024-44824-z) y se consultaron sus métodos y suplemento. El repositorio fijado es [`bowang-lab/MedSAM`, commit `d71e8a1a99ad751840a22a7fa3ecfb4166fb1488`](https://github.com/bowang-lab/MedSAM/tree/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488).

| Elemento | Evidencia verificada antes de ejecutar |
| --- | --- |
| Variante | `vit_b`, encoder de imagen y decoder de máscara MedSAM; prompt de caja |
| Pesos | [`medsam_vit_b.pth`, depósito Zenodo 10689643, versión 1.0](https://zenodo.org/records/10689643) |
| Autores del depósito | Jun Ma y Bo Wang; publicado el 21 de febrero de 2024 |
| Tamaño exacto publicado | 375 049 145 bytes |
| Checksum publicado | MD5 `3bb6db55bd0c9ca30b61248bca72f8d6` |
| Licencia del código | [Apache-2.0, archivo LICENSE del commit](https://github.com/bowang-lab/MedSAM/blob/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488/LICENSE) |
| Licencia declarada de este depósito de pesos | `CC-BY-4.0`, metadatos oficiales de la [API Zenodo](https://zenodo.org/api/records/10689643) |
| Comprobación local | Tamaño y MD5 coinciden con el depósito; SHA-256 `34b34b78c1d18cb8c6bf84cf9c00e135d6d6c965699f3c0e31ef1bc9dcb5be74` |

El depósito contiene un único checkpoint; la declaración de licencia procede del registro oficial, no de un archivo LICENSE dentro de los pesos. La carpeta Google Drive enlazada por el README tenía un aviso de uso para investigación: **no se trasladan términos entre distribuciones ni se supone identidad binaria sin comprobarla**. Este ensayo usa el depósito Zenodo identificado y finalidad de investigación. Los pesos no entrarán en Git, en el contexto de construcción ni en una imagen Docker distribuida.

El [dossier del artefacto](model-provenance/medsam-vit-b.json) registra además el archivo `tar.gz` de código de 33 327 385 bytes, SHA-256 `71fb5ac457805d9c0df0df0833b1bbd6a3da9489795b1d5b6c3a6377f11ef916`, y los 15 archivos Python del paquete oficial seleccionado, hash de árbol `b471fbce67496842f908f606dbe50205b3288c90ed9d99a0ab5f7455d110440e`. No se ejecutó `setup.py`, cuyo instalador añade dependencias MONAI/CUDA innecesarias para esta ruta; se expone únicamente el paquete verificado mediante `PYTHONPATH`. La inspección estática del pickle encontró tres globales esperados de Torch/colecciones; no constituye una prueba formal de seguridad ni sustituye la carga restringida.

El [gate de procedencia](model-provenance/medsam-vit-b.gate.json) conserva dos incertidumbres mayores: falta una auditoría exhaustiva del corpus y de los conjuntos de desarrollo del artefacto exacto. La ejecución CPU ya quedó comprobada en ambos casos evaluables. Los hallazgos restantes se refieren a evidencia incompleta, **no a una conclusión de contaminación por entrenamiento**. La relación conocida con HCC-TACE-Seg se especifica a continuación y no se oculta detrás de la etiqueta genérica del gate.

### Relación con HCC-TACE-Seg

Hay evidencia más específica que «corpus desconocido»: la **tabla suplementaria 1** del [suplemento oficial](https://static-content.springer.com/esm/art%3A10.1038%2Fs41467-024-44824-z/MediaObjects/41467_2024_44824_MOESM1_ESM.pdf) enumera `HCC-TACE-Seg*`, objetivo *Liver cancer*, **70 exploraciones**. El asterisco significa conjunto de validación externa de los autores. La tabla 16 identifica la colección mediante DOI TCIA `10.7937/TCIA.5FNA-0924`.

No se interpreta ese asterisco como entrenamiento: el artículo declara que los conjuntos externos no participaron en él. Tampoco permite afirmar que nuestros casos sean una nueva validación externa independiente del desarrollo y comparación del método: la misma colección ya se utilizó como benchmark de los autores. El suplemento consultado no identifica qué pacientes forman esas 70 exploraciones ni demuestra validación de máscaras POST después de TACE. Nuestro ensayo sigue sin referencia POST independiente y utiliza cajas derivadas de otra predicción, no cajas expertas equivalentes a las del estudio original.

## Entradas congeladas

| Caso | Procedencia histórica del baseline | TC usada por el refinador | Baseline exacto |
| --- | --- | --- | --- |
| HCC_006 | PRE adquisición 3, `Mass` 2 y `Liver` 1 publicados | POST adquisición 1; 103 × 512 × 512 | `output/post-boundary/run-20261113T211925-17cd15d6/refined-2mm.npz`; 11 355 vóxeles |
| HCC_002 | PRE adquisición 1, `Mass` 2 y `Liver` 1 publicados | POST adquisición 1; 97 × 512 × 512 | `output/post-boundary/run-20261113T212052-93e1217e/refined-2mm.npz`; 413 118 vóxeles |
| HCC_005 | PRE adquisición 3, `Mass` 2 y `Liver` 1 publicados | POST adquisición 3 | **No evaluable para este refinamiento:** la ROI de localización superó cuatro millones de vóxeles nativos antes de la inferencia del modelo baseline |
| HCC_004 | PRE adquisición 2, `Mass` 2 y `Liver` 1 publicados | POST adquisición 3 | **No evaluable para este refinamiento:** el registro rígido no pasó los controles numéricos de solapamiento o movimiento, antes de inferir la máscara baseline |

HCC_005 se añadió antes de observar resultados de MedSAM, con selección explícita en `tmp/medsam-slice/control-HCC005.json`. Su preparación se detuvo en el límite de ROI del método baseline. El único reemplazo HCC_004, seleccionado en `tmp/medsam-slice/control-HCC004.json`, se detuvo por los controles del registro rígido. **Ninguno es una medida de desempeño de MedSAM**, que no llegó a recibir esos casos. No se amplían límites ni se cambian fases para forzar candidatas; conforme a la regla declarada se detiene la búsqueda de más controles. Estos dos estados no evaluables permanecen en el registro.

Para HCC_006 y HCC_002 se usarán los archivos ya verificados, sin regenerar MedSAM2, recalcular el registro ni usar la salida nnInteractive.

El manifiesto identificará SOP ordenados, series, adquisiciones, retícula y hashes; los nombres de caso y recuentos no sustituyen esa identidad. El modelo nuevo recibe imagen POST y cajas derivadas exclusivamente de la máscara POST congelada. La información PRE figura en la procedencia del baseline, no como entrada de MedSAM.

## Caja automática y cortes evaluados

Sea \(M_0[z,y,x]\) el baseline. Se define de una sola vez

\[
S=\{z:\sum_{y,x}M_0[z,y,x]>0\},\qquad
U_z=M_0[z-1]\cup M_0[z]\cup M_0[z+1].
\]

Los vecinos fuera de la adquisición se tratan como vacíos. Para cada \(z\in S\), se toman los extremos **inclusivos de índices de píxel** de \(U_z\) y se amplían 5 mm en cada dirección:

\[
B_z=\left[x_{\min}-\frac{5}{\Delta x},\ y_{\min}-\frac{5}{\Delta y},\
x_{\max}+\frac{5}{\Delta x},\ y_{\max}+\frac{5}{\Delta y}\right].
\]

Las coordenadas se conservan como flotantes y se limitan al dominio `[0,W] × [0,H]`; se registrará si se recorta el margen. Son cajas de prompt, no recortes aplicados a la imagen o máscara. El margen es una decisión de ingeniería fijada antes del resultado, **no un parámetro clínico validado**. No se probarán otros márgenes para escoger el que mejor se vea.

La misma regla se aplica a todos los casos evaluables y cortes seleccionados; no hay excepción para app55. En HCC_006, app55 corresponde a `z=54`; su caja utiliza los baselines `z=53,54,55`. Por tanto puede abarcar la extensión observada en los vecinos sin ordenar al modelo rellenarla. Ninguna caja se calcula a partir de una predicción MedSAM recién generada. La decisión de cada corte sigue dependiendo indirectamente del baseline; independencia entre llamadas no equivale a independencia de referencia.

El [entrenamiento oficial](https://github.com/bowang-lab/MedSAM/blob/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488/train_one_gpu.py) construye extremos de índices y los perturba. El [prompt encoder](https://github.com/bowang-lab/MedSAM/blob/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488/segment_anything/modeling/prompt_encoder.py) añade internamente `0.5`; el adaptador no repetirá ese desplazamiento. Los prompts nativos y escalados a 1024 se guardarán para auditar la correspondencia.

## Preparación de imagen y retorno nativo

Se combinarán dos etapas oficiales explícitas: la preparación CT de [`pre_CT_MR.py`](https://github.com/bowang-lab/MedSAM/blob/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488/pre_CT_MR.py) y el procesamiento de una imagen en [`MedSAM_Inference.py`](https://github.com/bowang-lab/MedSAM/blob/d71e8a1a99ad751840a22a7fa3ecfb4166fb1488/MedSAM_Inference.py). **No se copia la selección de cortes por ground truth ni la eliminación de componentes pequeñas del script de entrenamiento.**

1. TC POST completa en HU: \(C=\operatorname{clip}(I,-160,240)\), ventana W400/L40 para tejidos blandos, documentada también en el artículo. Calcular \(a=\min C\), \(b=\max C\) sobre el volumen y \(Q=\operatorname{uint8}[255(C-a)/(b-a)]\). Si \(b=a\), rechazar la entrada degenerada. La máscara de validez no se usa para alterar la TC; las intensidades de padding quedan en esta operación, y se comprobará explícitamente que no aparezca foreground en padding.
2. Para cada plano de \(S\), repetir \(Q_z\) en tres canales. Usar el axial completo, no un recorte de la caja. Redimensionar a 1024 × 1024 con `skimage.transform.resize`, `order=3`, `preserve_range=True`, `anti_aliasing=True` y modo predeterminado `reflect`.
3. Convertir el resultado redimensionado a `uint8`, como el script de inferencia, y normalizar por mínimo y máximo **de esa imagen** a `[0,1]`, denominador mínimo `1e-8`. Entregar tensor `float32` de forma `[1,3,1024,1024]` al `image_encoder`. No usar normalización ImageNet ni `SAM.preprocess` adicional. Un plano constante sigue esta regla; no se elimina silenciosamente.
4. Escalar la caja: \(B_z^{1024}=1024 B_z/[W,H,W,H]\). El prompt encoder recibe solo caja (`points=None`, `masks=None`); el decoder usa `multimask_output=False`.
5. El decoder produce logits a 256 × 256. Aplicar **primero** sigmoid, después interpolación bilineal a la forma axial nativa con `align_corners=False`, y finalmente umbral estricto `>0.5`:

\[
M_1[z]=\mathbf 1\{\operatorname{resize}_{H,W}[\sigma(L_z)]>0.5\},\quad z\in S;
\qquad M_1[z]=M_0[z],\quad z\notin S.
\]

Interpolar probabilidades no es en general equivalente a interpolar logits y aplicar sigmoid después. Se conserva el orden oficial. El espaciado, origen y orientación de la salida siguen siendo los de la TC POST: la ampliación interna a 1024 no crea resolución física nueva. La variante de exportación del script de entrenamiento usa otros detalles (`mode=constant` y ausencia del segundo cast); no se mezclará silenciosamente con la ruta de inferencia elegida.

Se conservarán todos los componentes predichos en cada plano, incluso fuera de la caja de prompt. No habrá intersección con baseline, hígado o ROI; tampoco cierre de huecos, componente mayor ni graph cut posterior en el brazo primario. El foreground inválido por padding debe provocar un estado explícito, no desaparecer mediante recorte. Una predicción vacía en un corte evaluado se registra como salida del modelo, no como ausencia anatómica confirmada.

## Ejecución, seguridad del artefacto y costes

La ruta oficial admite `--device cpu`; esto no demuestra que sea rápida o que quepa en el entorno local. Antes del ensayo se verificará el checkpoint y el código fijado. El loader oficial deberá ejecutarse con `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`; un fallo de carga no autoriza volver a deserialización irrestricta. El entorno de investigación y sus versiones reales se registrarán aparte de la app.

Se ejecutarán los casos secuencialmente, con número de hilos y límite de recursos fijados por el runner. Se medirá carga, tiempo por corte, total y pico RSS. Los 16 planos ocupados iniciales de HCC_006 y los 41 de HCC_002 no implican un tiempo conocido: el encoder 1024 se calcula de nuevo por imagen. Un ensayo técnico pequeño puede comprobar factibilidad y estimar duración; no servirá para ajustar caja, ventana o umbral mirando app55.

La salida experimental tendrá forma nativa completa, lista explícita de planos evaluados y heredados, cajas por corte, probabilidades solo de planos evaluados si se conservan, máscaras antes/después y manifiesto de código, pesos y fuentes. Se escribirá en un directorio nuevo, fuera de Git y del almacén de candidatas; no reemplazará una anotación ni la selección activa de la aplicación.

## Reproducir en Docker

La imagen opcional `medsam-research` ya se construyó desde `app`: añade `scikit-image==0.26.0`, `matplotlib==3.11.2` y el paquete oficial seleccionado mediante [`prepare_medsam.py`](../../experiments/prepare_medsam.py). El código se verifica antes de extraerse; no se ejecuta su instalador. La imagen declara `PYTHONPATH=/opt/medsam:/app` y `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1`. Construirla comprueba instalación e integridad; la factibilidad CPU procede de las dos ejecuciones descritas abajo, no de la construcción por sí sola.

Desde `hcc-tace-lab`, definir las rutas absolutas antes de cualquier comando Compose, porque el archivo completo se interpola incluso para preparar pesos:

```sh
export HCC_UID="$(id -u)"
export HCC_GID="$(id -g)"
export HCC_DATA_DIR="$PWD/data"
export HCC_MEDSAM_MODEL_ROOT="$PWD/../tmp/medsam-slice"
export HCC_MEDSAM_BASELINE="$PWD/../output/post-boundary/run-20261113T211925-17cd15d6"
export HCC_MEDSAM_OUTPUT="$PWD/../output/post-medsam-slice"
mkdir -p "$HCC_MEDSAM_MODEL_ROOT" "$HCC_MEDSAM_OUTPUT"

docker compose -f compose.yaml -f experiments/compose.medsam.yaml \
  --profile research build medsam-research
```

`HCC_MEDSAM_MODEL_ROOT` es el directorio que contiene `medsam_vit_b.pth`. La ruta mostrada reutiliza el peso local comprobado. La preparación explícita siguiente descarga desde el depósito fijado cuando el archivo no existe; si ya existe, verifica tamaño y SHA-256. Un archivo incompatible se rechaza en lugar de sustituirse silenciosamente:

```sh
docker compose -f compose.yaml -f experiments/compose.medsam.yaml \
  --profile research-setup run --rm --no-deps medsam-assets
```

Ese servicio tiene red para obtener el peso y solo monta su directorio; no accede a TC, baseline ni anotaciones. El servicio de investigación, en cambio, no tiene red. Para verificar un peso existente y ejecutar HCC_006:

```sh
docker compose -f compose.yaml -f experiments/compose.medsam.yaml \
  --profile research run --rm --no-deps medsam-research \
  python -m experiments.prepare_medsam verify --destination /models

docker compose -f compose.yaml -f experiments/compose.medsam.yaml \
  --profile research run --rm --no-deps medsam-research
```

[`compose.medsam.yaml`](../../experiments/compose.medsam.yaml) limita la inferencia a dos CPU y 7 GiB, monta `/data`, `/models` y `/baseline` solo para lectura y `/research-output` para las salidas. No monta `/annotations`. El [runner](../../experiments/post_medsam_slice.py) crea una carpeta nueva por corrida. El servicio `app` no se modifica.

Cuando termine HCC_006, repetir con el control congelado HCC_002, sin cambiar parámetros:

```sh
export HCC_MEDSAM_BASELINE="$PWD/../output/post-boundary/run-20261113T212052-93e1217e"
docker compose -f compose.yaml -f experiments/compose.medsam.yaml \
  --profile research run --rm --no-deps medsam-research
```

Para producir tablas y figuras de una corrida terminada, sustituir `RUN_ID` por su nombre real y elegir un directorio nuevo. El [plotter](../../experiments/plot_medsam_slice.py) verifica fuentes y artefactos; no vuelve a ejecutar MedSAM:

```sh
docker compose -f compose.yaml -f experiments/compose.medsam.yaml \
  --profile research run --rm --no-deps medsam-research \
  python -m experiments.plot_medsam_slice \
  --data /data --run /research-output/RUN_ID \
  --output /research-output/figures-RUN_ID
```

## Evaluación predeclarada y decisión

Solo HCC_006 y HCC_002 tienen baseline compatible para esta comparación; los otros dos casos conservan su estado no evaluable. La revisión cubrirá la unión de máscaras antes/después en todos los cortes, vecinos de contexto y vistas ortogonales, usando W400/L40 y W800/L200 solo para visualizar. Se verificarán la identidad DICOM, forma, binariedad, hashes, padding y herencia exacta fuera de \(S\).

Los descriptores serán volumen de cada máscara, añadidos/retirados, pérdida de cortes terminales, componentes 2D y 3D, áreas y saltos del centroide en milímetros, cambios interiores y extensiones fuera de las cajas o ROI históricas. Las [definiciones matemáticas](post-neural-refinement.md#qué-calcula-y-cómo-leer-los-números) se mantienen. El conjunto de discrepancia entre vecinos en app55 se reportará como descriptor, nunca como objetivo que obligue a rellenar ni como número de errores.

**Condición para considerar adopción:** cambio visual defendible del problema HCC_006 sin regresiones materiales observadas en el resto de la estructura ni en HCC_002, integridad técnica completa y coste compatible con el uso. No basta disminuir un salto o el número de componentes. Esto es una decisión de ingeniería sobre dos casos de desarrollo; no demuestra exactitud, generalización a los 105 casos de la colección ni no inferioridad clínica. Los dos controles no evaluables no cuentan como resultados favorables ni desaparecen del registro. La anatomía correcta exige una [referencia experta independiente](annotation-kit.md).

Si el brazo directo introduce cambios no defendibles, se conserva como resultado negativo y no se promueve. Este protocolo no incluye un ensayo automático de variantes limitadas a una banda ni la mezcla posterior de máscaras. Cualquier enfoque posterior requerirá una decisión y protocolo separados; no se presentará como la salida directa de MedSAM.

## Resultados

### HCC_006: transición 55→56 menor, cambios importantes en el resto

Corrida `output/post-medsam-slice/run-20261114T023737-c4d0ac1e`. Se utilizó POST adquisición 1; la procedencia histórica del baseline es PRE adquisición 3, `Mass` segmento 2 y `Liver` segmento 1. El archivo baseline se copió exactamente, SHA-256 `511fcc15336070f0aae794e418e97edcea5af7e5fca0188b60e2217c813bf940`, sin volver a registrar ni generar MedSAM2.

Pasaron la relectura de fuentes CT, SOP ordenados, retícula, cajas calculadas desde el baseline, planos heredados y hashes de todos los artefactos. Las 103 filas descriptivas recalculadas coinciden exactamente con las del paquete visual. `probabilities.npz` contiene `slice_indices` enteros y probabilidades `float32` de forma **16 × 512 × 512**: índices `49–64`, valores finitos dentro de `[0,1]` y umbral `>0.5` que reproduce exactamente la salida nativa de esos planos. No contiene probabilidades inventadas para los otros 87 cortes, que fueron heredados. Esta coincidencia comprueba el retorno y guardado, no calibración probabilística ni precisión anatómica.

| Descriptor | Baseline MedSAM2 + 2 mm | MedSAM ViT-B por corte |
| --- | ---: | ---: |
| Vóxeles | 11 355 | 10 923 |
| Volumen de la máscara POST | 17,3264 cm³ | 16,6672 cm³ |
| Añadidos / retirados | — | 2 108 / 2 540 |
| Total cambiado respecto al baseline | — | 4 648; 40,934 % |
| Componentes 3D de conectividad 6 | 11 342; 10; 2; 1 | 10 922; 1 |
| Cortes ocupados, array / app | 49–64 / 50–65 | 49–64 / 50–65 |
| Núcleo inicial >2 mm retenido | 7 607 | 6 644 |

No hubo foreground en padding, fuera de la ROI histórica ni en límites nativos. No desapareció ningún corte inicialmente ocupado. **El pequeño cambio neto de volumen oculta un intercambio amplio de píxeles:** todos los 16 planos evaluados cambian y se retiran 963 vóxeles del núcleo geométrico inicial. Ese núcleo no es ground truth, pero su cambio impide describir el resultado como un retoque superficial restringido al borde.

| Corte o transición | Antes | Después |
| --- | ---: | ---: |
| App54 (`z=53`), píxeles | 635 | 561 |
| App55 (`z=54`), píxeles | 462 | 935 |
| App56 (`z=55`), píxeles | 709 | 773 |
| App57 (`z=56`), píxeles | 901 | 731 |
| Salto del centroide en plano, app54→55 | 5,237 mm | 5,819 mm |
| Salto del centroide en plano, app55→56 | 6,958 mm | 0,313 mm |
| Discrepancia entre ambos vecinos en app55 | 97 píxeles | 9 píxeles |

En app55 se añaden 473 píxeles y no se retira ninguno. Las vistas W400/L40 y W800/L200 muestran que la nueva extensión incluye una región de menor intensidad junto al foco brillante y próxima a la estructura lineal brillante vecina. El salto 55→56 disminuye, pero el de 54→55 aumenta y aparece una región marcada mucho mayor en app55. No basta interpretar el menor salto como un contorno correcto ni identificar el tejido añadido por su color.

La revisión de las cinco páginas del atlas y las vistas ortogonales muestra cambios fuera de esos dos cortes: se reduce la extensión gris del baseline en app53–54 y en buena parte de app57–63, mientras aparecen añadidos en otros bordes y en los extremos. App65 conserva volumen parecido (288→301 píxeles), pero intercambia 115 añadidos y 102 retirados; su salto respecto al corte anterior pasa de 1,459 a 2,971 mm. Las tres componentes secundarias iniciales desaparecen y queda otra de un vóxel. Estos resultados son mixtos y requieren la comparación completa, no únicamente el foco 55–56.

Carga del modelo: 2,999 s; preparación e inferencia: 179,947 s; runner completo: **185,419 s**, aproximadamente 3,09 minutos. Pico RSS: **3 630,199 MiB**, aproximadamente 3,55 GiB. Se ejecutó con Torch 2.5.1, scikit-image 0.26.0 y dos hilos CPU dentro del límite de 7 GiB. El tiempo por plano estuvo aproximadamente entre 10,85 y 12,83 s. Esta es evidencia de factibilidad local para 16 planos, no un benchmark universal ni una estimación verificada de HCC_002.

Paquete visual local: `output/post-medsam-slice/figures-HCC006-final`, con cinco páginas de atlas, focos en ambas ventanas, vistas ortogonales y las 103 filas CSV/JSON. Sus hashes también se comprobaron. La máscara experimental y las imágenes permanecen fuera del repositorio; la candidata activa de la aplicación no se sustituyó.

### HCC_002: el control tampoco conserva la estructura sin cambios importantes

Corrida `output/post-medsam-slice/run-20261114T024116-d900e4a7`, POST adquisición 1 y procedencia histórica PRE adquisición 1, `Mass` 2 y `Liver` 1. Se conservó el baseline exacto SHA-256 `6fbe0b8318d6820707c5f59169a9f3a8e729a0121656a80dff09fcd6e07d04a6`. Pasaron las mismas comprobaciones de fuentes, retícula, prompts y hashes; las 97 filas descriptivas recalculadas coinciden exactamente con las del paquete visual. Las probabilidades nativas `float32`, **41 × 512 × 512**, corresponden a `z=46–86` y reproducen exactamente la máscara con `>0.5`; los otros 56 planos se heredan sin reevaluarlos.

| Descriptor | Baseline MedSAM2 + 2 mm | MedSAM ViT-B por corte |
| --- | ---: | ---: |
| Vóxeles | 413 118 | 435 835 |
| Volumen de la máscara POST | 630,3680 cm³ | 665,0314 cm³ |
| Añadidos / retirados | — | 51 609 / 28 892 |
| Total cambiado respecto al baseline | — | 80 501; 19,486 % |
| Componentes 3D de conectividad 6 | 413 112; 4; 2 | 435 781; 16; 14; 12; 12 |
| Cortes ocupados, array / app | 46–86 / 47–87 | 46–86 / 47–87 |
| Núcleo inicial >2 mm retenido | 378 032 | 360 819 |

No aparecen vóxeles en padding, fuera de la ROI histórica ni en límites nativos; tampoco se pierden planos ocupados. Sin embargo, cambian los 41 planos evaluados y se retiran **17 213 vóxeles del núcleo geométrico inicial**. El aumento neto de volumen no resume estos intercambios ni representa una variación clínica: se compara la misma TC POST procesada por dos métodos.

| Corte o transición de HCC_002 | Antes | Después |
| --- | ---: | ---: |
| App47 (`z=46`), píxeles | 1 145 | 3 885 |
| App66 (`z=65`), componentes 2D de conectividad 4 | 1 | 3 |
| Salto del centroide en plano, app65→66 | 0,931 mm | 10,205 mm |
| Salto del centroide en plano, app66→67 | 1,062 mm | 10,277 mm |
| App86 (`z=85`), píxeles | 1 557 | 6 208 |
| Salto del centroide en plano, app86→87 | 2,356 mm | 23,161 mm |

La selección de planos no impide cambios en sus extremos ni variaciones entre imágenes consecutivas: app86 añade 4 686 píxeles y retira 35, mientras app87 queda en 1 796 frente a 1 087 iniciales. Disminuyen algunos saltos, como app47→48 (12,792→1,981 mm), pero aparecen otros mayores en el interior y al final. Estos descriptores motivan revisar las imágenes; no establecen por sí solos qué contorno es anatómicamente correcto.

Las vistas verificadas muestran una región mucho más amplia en el primer plano ocupado, app47, y cambios alternantes del borde en app65–67. Cerca del extremo opuesto, app86 delimita una región amplia y app87 una región lateral pequeña; las vistas ortogonales también muestran escalones. Los focos en ambas ventanas permiten reconocer que algunos intercambios de borde abarcan zonas grises amplias, no solo puntos aislados. No se asigna identidad tisular a esas zonas. El paquete local `output/post-medsam-slice/figures-HCC002-final` conserva diez páginas de atlas, ambos focos, vistas ortogonales y las 97 filas CSV/JSON; sus archivos se verificaron contra los hashes del manifiesto.

Carga del modelo: 2,423 s; preparación e inferencia: 460,379 s; runner completo: **466,889 s**, aproximadamente 7,78 minutos. Pico RSS: **3 624,141 MiB**, aproximadamente 3,54 GiB. El tiempo por plano estuvo entre 10,81 y 13,50 s en el mismo entorno Linux aarch64, Python 3.12.16 y dos hilos CPU. Los costes corresponden a estas ejecuciones locales, no a todos los estudios de la colección.

### Decisión

**Se conserva como comparador de investigación y no se incorpora a la aplicación.** La transición concreta 55→56 de HCC_006 es más continua, pero el cambio amplio del propio caso y las nuevas discontinuidades del control no sustentan la condición predeclarada de adopción. No se seleccionan manualmente los planos favorables ni se mezcla esta salida con el baseline para ocultar los demás cambios.

El ensayo aporta un refinador independiente entre planos, reproducible y técnicamente comprobado, junto con evidencia descriptiva de sus límites. No demuestra que toda modificación sea un error: establecerlo exige referencia POST independiente. Tampoco demuestra no inferioridad en los 105 casos. Las métricas del artículo original no se transfieren a estas predicciones, y los estados no evaluables de HCC_005 y HCC_004 permanecen documentados.

## Verificación de implementación y conservación de la aplicación

Después de reconstruir las imágenes finales se ejecutó la suite requerida, con los controles públicos HCC_002 habilitados:

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app \
  python -m unittest discover -s tests -v
```

Resultado: **327 pruebas en 141,414 s; 322 aprobadas y cinco omitidas** por dependencias opcionales ausentes de la imagen principal. Esas cinco se ejecutaron después en la imagen final `medsam-research`: las dos comprobaciones del preprocesamiento y las tres exportaciones de figuras MedSAM, nnInteractive y condicionamiento de prompts. **Las cinco aprobaron en 6,301 s.** Los registros locales son `output/post-medsam-slice/tests-backend.log` y `tests-optional.log`. Son comprobaciones de software, procedencia y exportación; no validan los contornos anatómicos.

La revisión final confirmó `/health` correcto y metadatos de la candidata activa idénticos antes y después. HCC_006 conserva `25b9f264bf5b40dfa2dfc54cb4b30f6b`, estado `unreviewed`, SHA-256 de máscara `511fcc15336070f0aae794e418e97edcea5af7e5fca0188b60e2217c813bf940`. Ninguna salida MedSAM de este experimento reemplazó esa candidata ni se presentó como referencia experta.
