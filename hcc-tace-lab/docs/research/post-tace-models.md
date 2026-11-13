# Modelos abiertos para segmentación POST-TACE

Consulta y comprobación de artefactos: **12 de noviembre de 2026**. Este documento distingue un artículo, su implementación y el checkpoint concreto: sus licencias y datos de entrenamiento pueden diferir.

## Decisión de ingeniería

La tarea es delimitar una lesión en una TC POST de HCC-TACE-Seg. Los resultados publicados de otros trabajos no se transfieren directamente a nuestras cajas obtenidas mediante registro PRE: el origen y la precisión del prompt forman parte del método evaluado. Disponemos de una máscara PRE publicada para localizar el objetivo, pero **no de una referencia POST experta con la que medir exactitud**. Un volumen plausible, una máscara suave o el acuerdo entre dos redes no sustituyen esa referencia.

La comparación realizada distingue tres funciones:

1. **MedSAM2 con contexto local**, como línea de base que ya funciona en CPU. Comparar sensibilidad al registro, tamaño del campo y ventana, preservando toda la predicción obtenida.
2. **nnInteractive**, como comparador 3D independiente: una caja axial obtenida del registro PRE, TC POST original como entrada y salida nativa. Completó los ensayos con un límite de 7 GiB; su coste limita el uso interactivo.
3. **TotalSegmentator para hígado y riñones**, como comprobación anatómica independiente de la localización. No convierte automáticamente sus órganos en un límite correcto de la lesión.

LesionLocator es el candidato más directamente relacionado con seguimiento longitudinal, pero su paquete y su consumo publicado lo hacen poco conveniente para este Docker. Se mantiene como siguiente experimento en un equipo con más memoria. No se propone entrenar desde cero antes de definir y revisar máscaras POST.

Recursos locales: Mac de 16 GB; Docker Linux aarch64, aproximadamente 8.32 GB decimales de RAM asignada, sin CUDA. MPS del Mac no está disponible dentro de ese contenedor. No se enviaron imágenes a servicios externos. La inferencia local no requiere una API de pago; tiempo, memoria y almacenamiento siguen siendo costes reales.

## Comparación de artefactos

| Opción | Función y entrada | Pesos comprobados | Licencias comprobadas | Decisión local |
|---|---|---|---|---|
| [MedSAM2](https://github.com/bowang-lab/MedSAM2/tree/332f30d420f1d1b08e2a79b3ae6a602458808383) | Caja en un corte y propagación sobre TC POST | EfficientTAM tiny FLARE25 RECIST; 71,662,031 bytes | Código Apache-2.0; condiciones del checkpoint documentadas en la ficha instalada | Línea de base; estudiar deriva y sensibilidad, sin dar por correcta la salida |
| [nnInteractive](https://github.com/MIC-DKFZ/nnInteractive/tree/e3126a029f46dcfd798c8428eec292f52cd438d6) | Red 3D con caja 2D, puntos u otras interacciones | v1.0 fold 0; 411,387,150 bytes | Código Apache-2.0; **pesos CC BY-NC-SA 4.0** | Piloto CPU completado; coste de memoria alto y sensibilidad a la caja |
| [LesionLocator](https://github.com/MIC-DKFZ/LesionLocator/tree/ef073baaa0dcba8dd036ec5a29f130db18c9d580) | Segmentación con punto/caja; seguimiento con estudio basal y seguimiento | ZIP oficial 14,073,990,099 bytes; contiene modelos de segmentación y seguimiento | Código GPL-3.0; **pesos CC BY-NC-SA 4.0** | Posponer ejecución; no descargar 14 GB sin una ruta de memoria viable |
| [TotalSegmentator](https://github.com/wasserth/TotalSegmentator/tree/d89e47f73d24f5fba732a715aeb6fdc0ae34526b) | Órganos en TC; contexto anatómico, no máscara tumoral de referencia | Modo rápido: tareas 297 y 298, 270,213,315 bytes comprimidos en conjunto | Código Apache-2.0; el proyecto declara `total` entre las tareas abiertas Apache-2.0 | Comparador anatómico local ejecutado y geometría verificada |
| [SegVol](https://github.com/BAAI-DCAI/SegVol/tree/5768c9795794a327a2629ce7f137ab03d5ea8378) | Segmentación volumétrica con caja, punto y texto | HF `pytorch_model.bin`, 723,726,667 bytes | Código MIT; **no encontramos licencia explícita del checkpoint en su repositorio HF** | No ejecutar ni redistribuir pesos hasta aclarar licencia; dependencias antiguas y código remoto requieren revisión |
| [VISTA3D](https://github.com/Project-MONAI/VISTA/tree/d4a8fe0dbf5cb4b76c531fccca8e29c1e6f6ee45) | Segmentación de clases anatómicas y objetivos indicados con puntos | HF safetensors, 871,894,112 bytes | El artefacto HF revisado separa código Apache-2.0 y pesos bajo NVIDIA NCLS, no comercial | Una licencia anunciada por el repositorio actual no se transfiere automáticamente al checkpoint anterior; CPU no ensayada |
| [SAM-Med3D](https://github.com/uni-medical/SAM-Med3D/tree/f3de1fa10da98e46f49f176773d2b1e306ba131f) | Segmentación 3D con prompts | HF Turbo, 402,163,626 bytes | Código Apache-2.0; **licencia de pesos no establecida en el repositorio HF revisado** | Menor prioridad; resolver licencia y consumo antes de ejecutar |
| [nnU-Net](https://github.com/MIC-DKFZ/nnUNet/tree/90cc65aa1958bd13678cc8c7b3b9496ee6f07d7e) | Marco para entrenar segmentadores con etiquetas adecuadas | No equivale a un checkpoint universal POST-TACE | Código Apache-2.0; cada modelo entrenado tiene su propia procedencia | Opción futura tras construir una referencia POST y separar pacientes para entrenamiento y evaluación |

Las licencias de código se leyeron en los archivos `LICENSE` de los commits indicados. Para pesos se revisaron los archivos del repositorio de modelos cuando existen. En TotalSegmentator los ZIP no aportan una licencia separada: la relación con Apache-2.0 se apoya en la declaración explícita de tareas del proyecto, no en una licencia inventada dentro del ZIP. No se ha verificado una autorización clínica de ninguno de estos artefactos.

## nnInteractive: receta y límites

El [artículo original](https://arxiv.org/abs/2503.08373v1) describe segmentación 3D interactiva. Se verificó su registro en la API de arXiv; no se atribuye un DOI de revista. El listado completo de datos usados para desarrollar el checkpoint descargado no quedó establecido. Por tanto, **no puede afirmarse que HCC-TACE sea una validación externa independiente** de este modelo.

Artefactos fijos:

- Código: `e3126a029f46dcfd798c8428eec292f52cd438d6`.
- [Modelo HF](https://huggingface.co/MIC-DKFZ/nnInteractive/tree/3f308d751c00644e4fde6f09c600264b393b21b5/nnInteractive_v1.0): revisión `3f308d751c00644e4fde6f09c600264b393b21b5`.
- Checkpoint: `nnInteractive_v1.0/fold_0/checkpoint_final.pth`.
- SHA-256 verificado localmente: `b3ac4421f85457bbd1aa0d87f5e67bcb7bc8e2ce6b824b6ac45077cc5d630ea9`.
- [Licencia de los pesos](https://huggingface.co/MIC-DKFZ/nnInteractive/blob/3f308d751c00644e4fde6f09c600264b393b21b5/nnInteractive_v1.0/LICENSE): CC BY-NC-SA 4.0.

La configuración liberada usa un parche 192×192×192. La implementación actual permite CPU; eso no demuestra que sea rápida ni que quepa en 8 GB. El checkpoint v1 admite **cajas 2D**: una caja con extensión mayor que uno en los tres ejes es rechazada como interacción 3D no soportada.

En nuestro adaptador se conserva `HU[z,y,x]`; la entrada es `[1,z,y,x]` y la caja es `[[z,z+1],[y0,y1],[x0,x1]]`, con límite superior exclusivo. No se aplica ventana ni conversión a 8 bits. Aunque alguna documentación llama XYZ a la matriz, lo decisivo es que prompts, matriz y salida compartan el mismo orden. No se reinterpreta una coordenada física como índice. El argumento de propiedades de imagen no realiza por sí mismo una normalización del espaciado.

Configuración explícita: `device=cpu`, `torch_n_threads=2`, compilación desactivada, interacciones comprimidas `blosc2`, deshacer desactivado. PyTorch 2.5.1, nnU-Net 2.7.0 y dynamic-network-architectures 0.4.4 se fijaron en un venv de investigación; no se modificaron las dependencias de la aplicación. El instalador generó un informe de pip y un inventario de versiones; `pip check` pasó.

El cargador oficial utiliza `torch.load(weights_only=False)`. Antes de ejecutarlo se comprobó el digest y se inspeccionaron estáticamente los opcodes del pickle; aparecieron tipos de tensor, NumPy y diccionarios esperados, sin globals inesperados observados. Esto reduce incertidumbre de procedencia, pero **no es una prueba formal de seguridad**. La ejecución se aisló sin red y con los datos montados como solo lectura.

Piloto HCC_006: PRE adquisición 3, etiqueta publicada Mass (2); POST adquisición 1, 103×512×512. La caja global procede del registro, corte z=56, filas 300:346, columnas 161:207. La variante de registro al hígado propone z=59, filas 314:360, columnas 175:219. No se eligieron cajas dibujando la lesión sobre el POST.

Docker confirmó `oom` y código 137 con límite de 6 GiB durante la primera predicción, después de cargar imagen e interacciones. Con 7 GiB ambos ensayos terminaron:

| Caja derivada del registro | Vóxeles | Volumen experimental | Inferencia y preprocesamiento | Pico RSS | Cortes ocupados | Fuera del ROI de localización |
|---|---:|---:|---:|---:|---|---:|
| Global | 6,975 | 10.643 cm³ | 238.99 s | 6,365.54 MiB | z54–62 | 0% |
| Ajuste al hígado | 15,226 | 23.233 cm³ | 243.03 s | 6,318.21 MiB | z33–64 | 25.38% |

Estos volúmenes no son mediciones tumorales validadas. La segunda salida muestra una prolongación distante en el montaje. El análisis 6-conexo encuentra una sola componente, por lo que no se trata de islas que puedan eliminarse conservando la componente mayor. Se mantuvo toda la salida. El Dice entre ambas predicciones es 0.2355: mide sensibilidad a la caja, no exactitud frente a una referencia. La sensibilidad a la caja y el coste cercano al límite de RAM desaconsejan usar este modelo como opción predeterminada en este Mac. El OOM de 6 GiB es un resultado de capacidad de ejecución, distinto de evaluar la calidad del modelo.

## LesionLocator: seguimiento no significa etiquetas POST públicas

El [trabajo CVPR 2025](https://openaccess.thecvf.com/content/CVPR2025/html/Rokuss_LesionLocator_Zero-Shot_Universal_Tumor_Segmentation_and_Tracking_in_3D_Whole-Body_CVPR_2025_paper.html) es pertinente por su separación entre localizar, segmentar y seguir. Sin embargo, su tabla de conjuntos de prueba incluye **66 imágenes HCC-TACE-Seg como segmentación de un solo tiempo**. Los experimentos longitudinales se realizan con melanoma de una colección interna: 159 estudios, evaluación mediante cinco particiones. El artículo no libera por ello máscaras POST-TACE para nuestra colección. La colección longitudinal sintética asociada tampoco constituye una referencia experta POST-TACE.

Los [pesos oficiales de Zenodo](https://zenodo.org/records/15174217) contienen cinco folds de segmentación y seguimiento. El archivo completo anuncia MD5 `1f9b8a64703346df9bcc532a0a32a07b`; no se calculó un SHA-256 local porque no se descargó. El listado del ZIP muestra aproximadamente 819 MB por checkpoint de segmentación y 1.4 GB por checkpoint de seguimiento. La CLI admite `-f 0`, pero **no se verificó una URL oficial independiente para descargar solo ese fold**.

La [documentación de consumo](https://github.com/MIC-DKFZ/LesionLocator/blob/ef073baaa0dcba8dd036ec5a29f130db18c9d580/documentation/inference_speed.md) describe un ejemplo donde el remuestreo físico expande mucho el volumen: una optimización reduce un pico aproximado de 29 a 15 GB. Son medidas del proyecto en otro equipo, no una predicción exacta para HCC_006. Aun así, exceden la RAM disponible en nuestro Docker.

Dos ensayos diferentes deben nombrarse correctamente:

```sh
# Segmentador POST con caja obtenida del registro PRE; un fold.
LesionLocator_segment -i post.nii.gz -p prompt.json -t box \
  -o output -m LesionLocatorCheckpoint -f 0 --disable_tta -npp 1 -nps 1 -device cpu

# Método longitudinal: usa además la máscara basal como entrada del modelo.
LesionLocator_track -bl pre.nii.gz -fu post.nii.gz -p pre_mask.nii.gz \
  -t prev_mask -o output -m LesionLocatorCheckpoint -f 0 -device cpu
```

El segundo método emplea una prior basal más fuerte que una caja. Su máscara de salida debe seguir calculándose con el POST; no corresponde presentarlo como si solo utilizara el PRE para obtener un centro.

## TotalSegmentator: comprobar la anatomía de la localización

Se descargaron las tareas abiertas `total` rápidas. Pesos oficiales:

| Archivo | Bytes | SHA-256 calculado localmente |
|---|---:|---|
| [Dataset297, 3 mm](https://github.com/wasserth/TotalSegmentator/releases/download/v2.0.0-weights/Dataset297_TotalSegmentator_total_3mm_1559subj.zip) | 135,386,075 | `0baa2c8de2975600eb31801dd5c1825cd2b356f794498659cf3348714c073394` |
| [Dataset298, 6 mm](https://github.com/wasserth/TotalSegmentator/releases/download/v2.0.0-weights/Dataset298_TotalSegmentator_total_6mm_1559subj.zip) | 134,827,240 | `b1832acde46c0f76cc9aba599e49e986c21c94e522ade4d920602b227e9d6c63` |

GitHub no publicó un digest para esos dos assets en los metadatos consultados. Los hashes de la tabla identifican nuestras descargas; **no son una comparación contra un hash publicado previamente**. También se inspeccionó el pickle sin deserializarlo antes de la ejecución.

Receta oficial equivalente del ensayo anatómico:

```sh
TotalSegmentator -i post-native.nii.gz -o organs --task total --fast \
  --roi_subset liver kidney_left kidney_right --device cpu --nr_thr_saving 1
```

La versión Python recibe un NIfTI generado desde HU verificados. La conversión usa columnas de la affine para los ejes x,y,z y transforma coordenadas DICOM LPS a NIfTI RAS. Después se comprueban forma y affine de la salida y se devuelve la matriz a ZYX original. Los resultados se comparan con las cajas del registro y las predicciones MedSAM2, **sin recortar estas a los órganos**.

El entorno de investigación usa una instalación mínima para inferencia sin interfaz: no instala componentes de render 3D ni informes. Por esa elección no puede afirmarse que todas las dependencias opcionales del paquete estén satisfechas; sí se comprueba que la ruta de inferencia usada importe y ejecute. Telemetría desactivada en configuración y red deshabilitada al inferir.

Resultado HCC_006, POST adquisición 1: 130.74 s de inferencia, 134.55 s total, pico RSS 4,226.12 MiB; cuota Docker de 2 CPU. El código oficial configura internamente diez hilos según la VM, aunque la cuota restringe el tiempo CPU. Las TC conservaron exactamente los hashes antes/después y la salida volvió a la affine y forma nativas verificadas.

| Predicción MedSAM2 del piloto | Coincidencia con riñón derecho predicho | Coincidencia con hígado predicho |
|---|---:|---:|
| Campo completo | 83.95% | 5.23% |
| Contexto 160 mm, registro global | 2.67% | 79.05% |
| Contexto 240 mm, registro global | 33.36% | 51.20% |
| Contexto 160 mm, ventana más amplia | 85.92% | 6.83% |
| Contexto 160 mm, registro al hígado | 0% | 20.10% |

Estas cifras corresponden a máscaras de los pilotos históricos: sus scripts identificaban la misma serie/adquisición, pero los NPZ iniciales no incluían un manifiesto de hashes por ejecución. No deben mezclarse sin advertencia con futuras repeticiones del runner que sí verifican hashes. La coincidencia renal alta aporta una comprobación independiente de la deriva observada. La caja global tiene 61.84% de sus vóxeles dentro del hígado predicho; la caja del ajuste hepático, 28.24%. Ninguno de esos valores elige por sí solo la localización correcta.

Una baja coincidencia con hígado puede deberse a error del registro, fallo del segmentador anatómico, una masa exofítica o un objetivo mal identificado. Una alta coincidencia con riñón es una señal para revisar, no un diagnóstico. Ninguno de estos solapamientos se llama Dice ni exactitud al carecer de verdad de referencia.

Existe además una tarea [liver_lesions](https://github.com/wasserth/TotalSegmentator/releases/download/v2.5.0-weights/Dataset591_ct_liver_lesions_842subj.zip) de 230,940,114 bytes, con SHA-256 publicado `b3bdd53de65dc64a46264edfdeb491b18e8f95973a691eeccfa530d1da1678a0`. Se descargó, se verificó ese digest y se inspeccionó su pickle. El [artículo asociado](https://doi.org/10.1007/s10278-025-01716-y) sí estudia una cohorte propia de 172 TC relacionadas con TACE. No encontramos HCC-TACE-Seg, TCIA o Moawad en el texto. El [dataset actual de 842 TC](https://zenodo.org/records/20272572) y el checkpoint CT de la tarea 591 no deben confundirse con la versión mixta CT/MRI descrita inicialmente. Esa diferencia impide asumir una correspondencia completa entre sus particiones y las del artículo. Se ensayó como comparador, manteniendo explícita la independencia no establecida. La tarea realiza su propio recorte anatómico del hígado: ceros fuera de ese campo representan regiones no evaluadas por el modelo de lesiones, no una garantía de ausencia de lesión.

### Resultado del comparador autónomo de lesiones

En HCC_006, POST adquisición 1, `liver_lesions` produjo una componente de **40 vóxeles (0.061 cm³)**, con centro a 116.52 mm de la localización global y 121.28 mm de la localización ajustada al hígado. No intersecta ninguna caja ni ROI de los tres registros comparados. No proporciona una candidata para el objetivo localizado; esto no equivale a medir sensibilidad o recall frente a una referencia que no tenemos.

El proceso tardó **1,068.49 s** de inferencia y 1,069.43 s total, con **4,112.22 MiB** de pico RSS. La cuota Docker real fue **4 CPU**, verificada con `docker inspect`; los resultados no deben compararse como si todos los métodos hubieran usado los mismos recursos. La geometría nativa y los hashes de las TC se conservaron. No hubo un recorte posterior para fabricar coincidencia con el ROI ni se escribió una candidata en la aplicación.

### Elección para la aplicación

Se conserva MedSAM2 con contexto local y registro hepático como **candidata experimental sin revisar** del protocolo 2. Su reducción de la deriva renal observada y su coste de ejecución justifican continuar el desarrollo, sin afirmar que su contorno sea exacto. nnInteractive y TotalSegmentator quedan como comparadores documentados; no se convierten en un ensemble, una votación ni una fuente de verdad de referencia. La integración y las comprobaciones de interfaz se documentan en el informe principal del protocolo.

## Opciones que requieren más verificación

- **SegVol:** [revisión HF](https://huggingface.co/BAAI/SegVol/tree/516701d5bef430604a13f5db7ce896149d075663), SHA-256 anunciado del binario `500f2758a8f989339b2b2baf09a819169bc87549795193d3cfe505726ac0b399`. El código propone `trust_remote_code=True`; habría que auditar y fijar también ese código. No se descargó ni ejecutó.
- **VISTA3D:** [revisión HF](https://huggingface.co/MONAI/VISTA3D-HF/tree/d86e7125856f1491a4ee6bb60b540a295b5d61a4), `model.safetensors`, SHA-256 anunciado `120ed013722a22780cc01b75cb5c18e4658d69879a983885abf8fa411c9f8f42`. El [LICENSE del artefacto](https://huggingface.co/MONAI/VISTA3D-HF/blob/d86e7125856f1491a4ee6bb60b540a295b5d61a4/LICENSE) es el que debe gobernar esta copia. No se verificó CPU local.
- **SAM-Med3D:** [revisión HF](https://huggingface.co/blueyo0/SAM-Med3D/tree/fc482a040ea69cdc9ae576a1d6bd9db02ab1994c), Turbo SHA-256 anunciado `899a46d04d3b70f723282ceb489149373558bf0aaba389a346f5ab57da5cdd3c`. Falta una licencia explícita del checkpoint en los archivos revisados.
- Los métodos de seguimiento que predicen centros o cajas, como [DeepLesionTracker](https://arxiv.org/abs/2012.04872), son candidatos a mejorar la **localización**, no reemplazos automáticos de una segmentación POST. No se ha completado su auditoría de pesos/dependencias y no se presentan como opciones ya instalables.

## Reproducibilidad y siguiente decisión

Los prototipos de estos comparadores, entornos, pesos, imágenes y resultados locales se mantienen fuera de Git, en `tmp/post-improvement/nninteractive` y `tmp/post-improvement/totalseg`. Estos prototipos concretos no se promueven a producción ni se presentan como runners reproducibles ya publicados. La versión MedSAM adoptada sí dispone de un runner en `experiments/`; para los comparadores se conservan las recetas, pines y registros de este informe. Los manifiestos enumeran URLs, revisiones, tamaños y hashes; los resultados registran caso, serie, adquisición, fuente PRE, geometría y parámetros. Estos comparadores no escribieron ninguna candidata en las anotaciones de producción. La auditoría final de sus cuatro matrices guardadas confirmó forma nativa, correspondencia física y SOP de nnInteractive, coincidencia de hashes CT para TotalSegmentator y cero predicciones sobre padding inválido. Esta comprobación posterior no sustituye hashes antes/después ausentes en los primeros pilotos.

Los dossiers y el informe del verificador se encuentran en `docs/research/model-provenance`. Los datos de entrenamiento o validación desconocidos se dejan ausentes para que el verificador los señale: rellenarlos con una lista vacía habría afirmado incorrectamente que no existen.

El desarrollo puede continuar incorporando una candidata experimental claramente sin revisar, con fuente, parámetros y fallos visibles. Para afirmar exactitud o respuesta al tratamiento necesitamos confirmar la lesión objetivo y obtener una referencia POST adjudicada. Con esa referencia se podrán medir Dice, distancia de superficie, diferencia de volumen y frecuencia de fallos en pacientes separados; tiempo y memoria ya pueden medirse sin ella. Los experimentos descritos aquí permiten decidir si una alternativa **corre y merece revisión**; todavía no establecen rendimiento clínico ni respuesta al tratamiento.
