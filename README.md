# Axivol Tracker

Seguimiento volumétrico del carcinoma hepatocelular en tomografía, antes y después de la quimioembolización transarterial (TACE).

Proyecto de titulación de **Dimitri Maciel**, Maestría en Ciencias Computacionales, Universidad Autónoma de Guadalajara.

## Material del proyecto

- [Póster científico R12: PDF horizontal de 120 × 90 cm](output/pdf/Poster_Tesis_Dimitri_Maciel_Horizontal_r12.pdf).
- [Vista previa del póster](output/pdf/Vista_previa_poster_horizontal_r12.png).
- [Guion de presentación de cinco minutos](docs/Guion_presentacion_5_minutos.txt).
- [Instalación, descarga de datos y pruebas](hcc-tace-lab/README.md).
- [Arquitectura y decisiones de interfaz](hcc-tace-lab/docs/decisiones.md).
- [Reporte técnico del caso HCC_002](output/avance_tecnico/reporte_HCC_002.json).

## Estado actual

El prototipo permite explorar series y adquisiciones de TC, ajustar ventana y nivel, superponer una máscara DICOM SEG publicada y calcular su volumen en la geometría original. Los datos permanecen en el equipo que ejecuta la aplicación.

El conjunto descargado en el entorno de desarrollo contiene 105 casos y 677 series. El ejemplo de volumetría corresponde al caso **HCC_002, adquisición PRE 1, etiqueta Mass (segmento 2)**: 324 430 vóxeles, 109 cortes vinculados y **495,04 cm³**. Es una comprobación técnica de lectura y medición de la máscara publicada.

El registro, la segmentación POST y la comparación longitudinal continúan pendientes. El volumen de la máscara no determina viabilidad ni respuesta al tratamiento. Las pruebas del caso piloto no equivalen a evaluar los 105 casos.

## Ejecutar el caso piloto

Se necesita Docker con Compose y conexión a internet para descargar el caso desde TCIA.

```sh
git clone https://github.com/dimitri-maciel/axivol-tracker.git
cd axivol-tracker/hcc-tace-lab
mkdir -p data
export HCC_UID=$(id -u) HCC_GID=$(id -g)
docker compose run --build --rm ingest catalog
docker compose run --rm ingest fetch --case HCC_002 --workers 3
docker compose up --build -d app
```

Abre [el visor local](http://127.0.0.1:8770/?case=HCC_002). El puerto se publica únicamente en `127.0.0.1`. El servicio de la aplicación monta los datos en solo lectura.

```sh
docker compose exec -T app python -m unittest discover -s tests -v
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

La segunda orden requiere el caso HCC_002 descargado. Los detalles de la ingesta completa y del anexo clínico están en la [guía de ejecución](hcc-tace-lab/README.md).

## Organización

- `hcc-tace-lab/`: aplicación, servicios Docker, ingesta TCIA y pruebas.
- `poster/`: generador del póster vigente, figuras derivadas y procedencia.
- `output/pdf/`: póster y vistas de revisión de la versión vigente.
- `output/avance_tecnico/`: reportes de ingesta y medición del caso público.
- `docs/`: guion para la presentación.

El repositorio publica una selección de la versión vigente. Los borradores y prototipos anteriores se conservan en el historial local de desarrollo.

## Datos y atribución

Los DICOM, los archivos descargados, el anexo clínico, las dependencias y los secretos no se incluyen en Git. El procedimiento de ingesta permite reconstruir el entorno a partir de la fuente pública.

Imágenes y máscaras: Moawad et al., **HCC-TACE-Seg**, The Cancer Imaging Archive, 2021. [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924), licencia [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Las figuras del póster son representaciones derivadas; sus transformaciones se documentan en los archivos de procedencia. La aplicación conserva la licencia MIT de html2canvas en su carpeta web.
