# Axivol Tracker

Aplicación local para explorar tomografías de carcinoma hepatocelular antes y después de TACE, revisar las segmentaciones publicadas y medir su volumen en la geometría original.

La interfaz está en inglés. Incluye dos visores, selección de serie y adquisición, ventana y nivel, zoom, navegación por cortes y exportación de reportes. La máscara PRE permite ajustar contorno y transparencia, corregir una copia con pincel y generar un refinamiento automático local del borde conservando el original. **Follow-up mask** genera una candidata POST con registro rígido global, refinamiento centrado en `Liver ∪ Mass` PRE y segmentación de los píxeles POST en un contexto físico local. También conserva el método asistido con región, semilla y límites HU. Las candidatas y el registro requieren revisión; las referencias POST expertas y la comparación longitudinal siguen pendientes. El volumen de un candidato no determina viabilidad ni respuesta al tratamiento.

## Iniciar

Requisitos: Docker con Compose y conexión a internet para descargar el caso público desde TCIA.

```sh
git clone https://github.com/dimitri-maciel/axivol-tracker.git
cd axivol-tracker/hcc-tace-lab
mkdir -p data
export HCC_UID=$(id -u) HCC_GID=$(id -g)
docker compose run --build --rm ingest catalog
docker compose run --rm ingest fetch --case HCC_002 --workers 3
docker compose run --rm ingest fetch --case HCC_006 --workers 3
docker compose run --rm --entrypoint python ingest -m hcclab.model_assets fetch
docker compose up --build -d app
```

Abre [Axivol Tracker](http://127.0.0.1:8770/?case=HCC_002). La aplicación se sirve únicamente en el equipo local y lee los datos sin modificarlos.

## Documentación

La generación POST usa Efficient MedSAM2 tiny en CPU. Los pesos se descargan a `data/models`, se verifican con SHA-256 y permanecen fuera de Git y de la imagen de aplicación. En el piloto HCC_002, la inferencia completa tarda aproximadamente 50 segundos. En **Follow-up mask**, pulsa **Generate automatically** y revisa después el resultado **Unreviewed**. El código upstream tiene licencia Apache-2.0; los pesos publicados especifican uso de investigación/educación y CC-BY-SA-4.0. Véanse las [fuentes y límites del modelo](hcc-tace-lab/docs/automatic-post-segmentation.md).

- [Ejecución, descarga de datos y pruebas](hcc-tace-lab/README.md).
- [Arquitectura y decisiones técnicas](hcc-tace-lab/docs/decisiones.md).
- [Interfaz y verificación en navegador](hcc-tace-lab/docs/interface-design.md).
- [Revisión y edición de una copia PRE](hcc-tace-lab/docs/pre-mask-review.md).
- [Refinamiento automático PRE: método, matemáticas y evaluación](hcc-tace-lab/docs/automatic-refinement.md).
- [Generación de candidatos POST: uso, método y evaluación](hcc-tace-lab/docs/post-segmentation.md).
- [Localización y segmentación automática POST: funcionamiento y límites](hcc-tace-lab/docs/automatic-post-segmentation.md).
- [Matemáticas de la segmentación, geometría y métricas](hcc-tace-lab/docs/post-segmentation-math.md).
- [Investigación POST: evidencia, modelos abiertos, experimentos y validación](hcc-tace-lab/docs/research/README.md).
- [Guía matemática del seguimiento con ejemplos](hcc-tace-lab/docs/research/post-mathematics.md).

## Estructura

```text
hcc-tace-lab/
  hcclab/           Lectura DICOM, geometría, operaciones y servidor
  web/              Interfaz del visor
  tests/            Pruebas de geometría, integración y navegador
  experiments/      Comparaciones reproducibles separadas de la aplicación
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

Las pruebas del método asistido utilizan una región pequeña de la adquisición 1 de HCC_002 para verificar geometría, superposición y exportación; esa región no es una anotación tumoral. La prueba automática procesa la adquisición POST completa en z y verifica fuentes, máscara nativa y persistencia. Al 12 de noviembre de 2026 pasaron 134 pruebas y se comprobó la generación y visualización automática en HCC_002 y HCC_006. Los candidatos conservan el estado `unreviewed`; las medidas de respuesta permanecen sin calcular. La evidencia y sus límites están en la [guía del método](hcc-tace-lab/docs/automatic-post-segmentation.md).

## Atribución

Datos e imágenes: Moawad et al., *HCC-TACE-Seg*, The Cancer Imaging Archive, 2021. [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924), licencia [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

La licencia MIT de html2canvas se conserva junto con la biblioteca en `hcc-tace-lab/web/`.
