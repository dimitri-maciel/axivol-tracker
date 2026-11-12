# Axivol Tracker

Aplicación local para explorar tomografías de carcinoma hepatocelular antes y después de TACE, revisar las segmentaciones publicadas y medir su volumen en la geometría original.

La interfaz está en inglés. Incluye dos visores, selección de serie y adquisición, ventana y nivel, zoom, navegación por cortes y exportación de reportes. La máscara PRE permite ajustar contorno y transparencia, y corregir una copia con pincel conservando el original. El editor **Follow-up mask** genera candidatos POST mediante una región, una semilla y límites HU, con revisión visual y exportación de la máscara y su procedencia. El registro entre estudios, las referencias POST expertas y la comparación longitudinal siguen pendientes. El volumen de un candidato no determina viabilidad ni respuesta al tratamiento.

## Iniciar

Requisitos: Docker con Compose y conexión a internet para descargar el caso público desde TCIA.

```sh
git clone https://github.com/dimitri-maciel/axivol-tracker.git
cd axivol-tracker/hcc-tace-lab
mkdir -p data
export HCC_UID=$(id -u) HCC_GID=$(id -g)
docker compose run --build --rm ingest catalog
docker compose run --rm ingest fetch --case HCC_002 --workers 3
docker compose up --build -d app
```

Abre [Axivol Tracker](http://127.0.0.1:8770/?case=HCC_002). La aplicación se sirve únicamente en el equipo local y lee los datos sin modificarlos.

## Documentación

- [Ejecución, descarga de datos y pruebas](hcc-tace-lab/README.md).
- [Arquitectura y decisiones técnicas](hcc-tace-lab/docs/decisiones.md).
- [Interfaz y verificación en navegador](hcc-tace-lab/docs/interface-design.md).
- [Revisión y edición de una copia PRE](hcc-tace-lab/docs/pre-mask-review.md).
- [Generación de candidatos POST: uso, método y evaluación](hcc-tace-lab/docs/post-segmentation.md).
- [Matemáticas de la segmentación, geometría y métricas](hcc-tace-lab/docs/post-segmentation-math.md).

## Estructura

```text
hcc-tace-lab/
  hcclab/           Lectura DICOM, geometría, operaciones y servidor
  web/              Interfaz del visor
  tests/            Pruebas de geometría, integración y navegador
  docs/             Documentación técnica
  compose.yaml      Servicios de aplicación e ingesta
  Dockerfile        Construcción de las imágenes
  requirements.txt  Dependencias de Python
```

Los datos descargados, máscaras, capturas, reportes generados y materiales de preparación de la exposición permanecen fuera del repositorio. Las máscaras candidatas se guardan en un volumen Docker separado. Las pruebas generan sus resultados localmente en `output/`.

## Verificar

Desde `hcc-tace-lab/`, con la aplicación encendida y HCC_002 descargado:

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

El caso de comprobación es **HCC_002, adquisición PRE 1, etiqueta Mass (segmento 2)**: 324 430 vóxeles y 495,04 cm³. Esta prueba verifica la lectura y medición de una máscara publicada; no evalúa el desempeño clínico ni toda la colección.

Las pruebas POST utilizan una región pequeña de la adquisición 1 de HCC_002 para verificar geometría, superposición y exportación. Esa región de prueba no es una anotación tumoral. Los candidatos conservan el estado `unreviewed`; las medidas de respuesta permanecen sin calcular.

## Atribución

Datos e imágenes: Moawad et al., *HCC-TACE-Seg*, The Cancer Imaging Archive, 2021. [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924), licencia [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

La licencia MIT de html2canvas se conserva junto con la biblioteca en `hcc-tace-lab/web/`.
