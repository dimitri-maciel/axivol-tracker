# Ejecutar HCC-TACE Lab

Ejecuta estos comandos desde esta carpeta. Se necesita Docker Desktop y espacio disponible para la colección de 28.57 GB más la extracción temporal. Docker publica el visor en `127.0.0.1:8770` y el contenedor de descarga solo escribe en `data/`.

```sh
mkdir -p data
export HCC_UID=$(id -u) HCC_GID=$(id -g)
docker compose run --build --rm ingest catalog
docker compose run --rm ingest fetch --case HCC_002 --workers 3
docker compose run --rm ingest clinical
docker compose up --build -d app
```

Abre [HCC_002](http://127.0.0.1:8770/?case=HCC_002). **Series y ajustes** permite elegir la adquisición dentro de una serie, la etiqueta SEG y ventana/nivel. Los recorridos se vinculan por posición relativa, sin afirmar alineación anatómica. El enlace de reporte descarga JSON con UID, adquisición, espaciado, etiqueta y estado de revisión.

Para toda la colección y comprobación de progreso:

```sh
docker compose run --rm ingest fetch --all --workers 4
docker compose run --rm ingest status
docker compose ps
```

Se puede ejecutar otra vez la ingesta: solo descarga las series que no tienen una marca de finalización con el conteo esperado. Cada descarga usa un archivo temporal, verifica el número de DICOM, extrae verificando el CRC del ZIP y registra el SHA-256 del archivo recibido. Los directorios completos se publican atómicamente. Un directorio previo incoherente se reporta y requiere revisión; no se borra automáticamente. El XLSX de TCIA se guarda separado en `data/clinical/` y no se sirve en la interfaz.

## Verificar

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

Las pruebas del caso piloto confirman que se separan dos adquisiciones PRE de 109 cortes, que ambas imágenes se decodifican y que se puede superponer la máscara publicada. Para `Mass` en HCC_002: 324,430 vóxeles, espaciado en plano de 0.78125 × 0.78125 mm, intervalo axial de 2.5 mm, volumen físico de 495.04 cm³. Es una comprobación de lectura y medición de la máscara fuente en un caso; no un resultado comparativo o de validación clínica.

`volume_change_percent` queda en `null`. Deben verificarse fase, cobertura, registro y correspondencia de lesión, y conseguir o generar una máscara POST adecuada antes de cuantificar cambio. La presencia de datos de un caso en disco no implica que sea elegible.

## Atribución

Moawad, A. W., Fuentes, D., Morshid, A., et al. (2021). *Multimodality annotated HCC cases with and without advanced imaging segmentation* [Data set]. The Cancer Imaging Archive. [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924). [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). El visor aplica ventana/nivel y, cuando se elige, añade una superposición de la máscara publicada.

Artículo de la colección: [Moawad et al., Scientific Data (2023)](https://doi.org/10.1038/s41597-023-01928-3). Publicación indicada por TCIA: [Morshid et al. (2019)](https://doi.org/10.1148/ryai.2019180021). Repositorio de datos: [Clark et al. (2013)](https://doi.org/10.1007/s10278-013-9622-7).

El botón de imagen usa [html2canvas](https://html2canvas.hertzen.com/), versión 1.4.1, licencia MIT, incluido localmente.
