# Póster científico

Versión vigente: **Horizontal R12**, de **120 × 90 cm**.

- [PDF para presentación e impresión](../output/pdf/Poster_Tesis_Dimitri_Maciel_Horizontal_r12.pdf).
- [Vista general](../output/pdf/Vista_previa_poster_horizontal_r12.png).
- [Detalle de avances y evaluación pendiente](../output/pdf/Detalle_avances_horizontal_r12.png).
- [Detalle de referencias y QR](../output/pdf/Detalle_referencias_qr_horizontal_r12.png).
- [Comprobaciones del PDF](validacion_horizontal_r12.json).

## Generar el PDF

El generador utiliza Python, ReportLab, Pillow y las fuentes Arial incluidas en macOS. Se ejecuta desde la raíz del repositorio:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r poster/requirements.txt
python poster/crear_poster_horizontal_r12.py
```

Se incluyen las figuras necesarias para reproducir el documento. No hace falta descargar los DICOM para regenerar este PDF. La aplicación y su ingesta usan el entorno Docker descrito en su propia guía.

## Alcance de la evidencia

La medición corresponde a **HCC_002, adquisición PRE 1, etiqueta Mass**: 324 430 vóxeles en 109 cortes vinculados y 495,04 cm³. Se conserva la geometría original; el suavizado de superficies se utiliza únicamente para visualización. La máscara fue publicada por TCIA, no generada por un modelo desarrollado en este proyecto.

El registro, la referencia POST y la comparación longitudinal siguen pendientes. Los 105 casos y 677 series describen el inventario disponible, no una evaluación de desempeño en toda la colección.

Las figuras mantienen su atribución a Moawad et al., HCC-TACE-Seg, TCIA, [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Los archivos `assets/evidencia_r2/procedencia.json` y `assets/evidencia_v4/procedencia.json` registran su procedencia.

El PDF fue revisado visualmente, tiene las fuentes incrustadas y contiene un QR vectorial verificado al repositorio de Axivol Tracker. Las cifras de ocupación del texto del informe de validación son aproximaciones basadas en las cajas de caracteres.
