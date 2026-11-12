# Arquitectura y decisiones técnicas

## Capas

| Módulo | Responsabilidad |
| --- | --- |
| `tcia.py` | Catálogo, descarga y verificación de archivos de TCIA. |
| `geometry.py` | Orden físico de cortes, compatibilidad espacial y volumen. No accede al navegador ni a la red. |
| `catalog.py` | Consulta del manifiesto y disponibilidad de estudios locales. |
| `dicom.py` | Lectura de píxeles, referencias DICOM SEG y representación de cortes. |
| `lab.py` | Operaciones de la aplicación compartidas por servidor y pruebas. |
| `server.py` | Adaptador HTTP para las operaciones y los archivos estáticos. |
| `web/` | Presentación, controles y carga de imágenes en el navegador. |

Esta separación permite comprobar la geometría sin abrir el visor y cambiar la presentación sin modificar las reglas de medición. Sigue el principio de [separación entre presentación, dominio y datos](https://martinfowler.com/bliki/PresentationDomainDataLayering.html).

## Almacenamiento y contenedores

El corpus DICOM y su manifiesto son la fuente de datos. Esta etapa no requiere una base SQL: se consulta un catálogo de archivos y se conservan los identificadores originales.

`compose.yaml` separa la ingesta, que escribe en `data/`, del visor, que monta esa carpeta en solo lectura. Los datos, archivos descargados, secretos y dependencias locales se excluyen de Git y del contexto de construcción de Docker.

## Medición y procedencia

- Las adquisiciones de una misma serie se separan antes de ordenar sus cortes.
- La posición y orientación DICOM determinan el orden espacial; el nombre del archivo no determina la posición.
- El volumen requiere espaciado uniforme y referencias SEG compatibles con la adquisición seleccionada.
- Cada resultado identifica caso, adquisición, etiqueta, conteo de vóxeles y geometría.
- Una máscara PRE no se utiliza para inferir el volumen POST ni una respuesta al tratamiento.
- Los estados de referencias ausentes, geometría incompatible y comparación pendiente se presentan explícitamente.

## Interfaz y pruebas

La selección de fuente pertenece a cada visor. Los cambios de caso, serie, adquisición o segmento invalidan las mediciones anteriores. La sincronización de desplazamiento relaciona posiciones relativas; no establece registro anatómico. Los detalles están en la [guía de interfaz](interface-design.md).

Las pruebas de `tests/` verifican reglas geométricas, el caso público HCC_002 y los controles reales del navegador. Los resultados, imágenes y reportes producidos al ejecutarlas son archivos locales regenerables y no se versionan. Los comandos están en la [guía de ejecución](../README.md).
