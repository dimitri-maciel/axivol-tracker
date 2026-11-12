# Arquitectura y decisiones técnicas

## Capas

| Módulo | Responsabilidad |
| --- | --- |
| `tcia.py` | Catálogo, descarga y verificación de archivos de TCIA. |
| `geometry.py` | Orden físico de cortes, compatibilidad espacial y volumen. No accede al navegador ni a la red. |
| `catalog.py` | Consulta del manifiesto y disponibilidad de estudios locales. |
| `dicom.py` | Lectura de píxeles, referencias DICOM SEG y representación de cortes. |
| `volume.py` | Carga cuantitativa HU, exclusión de padding y validación de la rejilla CT nativa. |
| `segmentation.py` | Componente conexa de una semilla dentro de ROI y rango HU, independiente de archivos e interfaz. |
| `mask_editing.py` | Correcciones manuales sobre una copia binaria, con pincel en milímetros y trazos reproducibles. |
| `candidates.py` | Persistencia inmutable, integridad y exportación de candidatos con procedencia. |
| `lab.py` | Operaciones de la aplicación compartidas por servidor y pruebas. |
| `server.py` | Adaptador HTTP para las operaciones y los archivos estáticos. |
| `web/` | Presentación, controles y carga de imágenes en el navegador. |

Esta separación permite comprobar la geometría sin abrir el visor y cambiar la presentación sin modificar las reglas de medición. Sigue el principio de [separación entre presentación, dominio y datos](https://martinfowler.com/bliki/PresentationDomainDataLayering.html).

## Almacenamiento y contenedores

El corpus DICOM y su manifiesto son la fuente de datos. Esta etapa no requiere una base SQL: se consulta un catálogo de archivos y se conservan los identificadores originales.

`compose.yaml` separa la ingesta, que escribe en `data/`, del visor, que monta esa carpeta en solo lectura. Los datos, archivos descargados, secretos y dependencias locales se excluyen de Git y del contexto de construcción de Docker.

El volumen Docker `annotations` conserva candidatos nuevos en `/annotations`, separado del corpus. Cada generación publica atómicamente un directorio con máscara NPZ y metadatos JSON. Sus identificadores son aleatorios; cambiar parámetros crea otra versión. Las huellas de los archivos fuente forman parte de la clave de caché HU. Al reutilizar un candidato se comprueban sus referencias y sus fuentes, conservando el resultado de la comprobación mientras no cambian tamaño, fecha de modificación o identidad de los archivos.

El primer método POST es crecimiento por umbral con conectividad de seis vecinos, implementado con SciPy. No emplea la máscara PRE, no remuestrea la adquisición ni aprende parámetros de pacientes. Se limita el volumen a 64 millones de vóxeles y la ROI a cuatro millones. Las razones, ecuaciones y limitaciones se explican en la [guía de segmentación](post-segmentation.md).

Las ediciones PRE parten del SEG publicado y usan su rejilla de referencias exactas. Se guardan como candidatos `PRE` separados, con hash del SEG y de las imágenes, segmento fuente, trazos y conteos de cambios. El navegador y el servidor usan la misma distancia física al trazo. El contorno mostrado es interior: no expande ni suaviza la máscara. Los detalles están en la [guía de revisión PRE](pre-mask-review.md).

## Medición y procedencia

- Las adquisiciones de una misma serie se separan antes de ordenar sus cortes.
- La posición y orientación DICOM determinan el orden espacial; el nombre del archivo no determina la posición.
- El volumen requiere espaciado uniforme y referencias SEG compatibles con la adquisición seleccionada.
- Cada resultado identifica caso, adquisición, etiqueta, conteo de vóxeles y geometría.
- Una máscara PRE no se utiliza para inferir el volumen POST ni una respuesta al tratamiento.
- Los estados de referencias ausentes, geometría incompatible y comparación pendiente se presentan explícitamente.
- Una máscara nueva se identifica como candidata sin revisar. Su volumen aparece en `post_candidate` o `pre_edited_candidate`, separado de la medición publicada; no habilita porcentajes de respuesta.

## Interfaz y pruebas

La selección de fuente pertenece a cada visor. Los cambios de caso, serie, adquisición o segmento invalidan las mediciones anteriores. La sincronización de desplazamiento relaciona posiciones relativas; no establece registro anatómico. Los detalles están en la [guía de interfaz](interface-design.md).

Las pruebas de `tests/` verifican reglas geométricas, el caso público HCC_002 y los controles reales del navegador. Los resultados, imágenes y reportes producidos al ejecutarlas son archivos locales regenerables y no se versionan. Los comandos están en la [guía de ejecución](../README.md).
