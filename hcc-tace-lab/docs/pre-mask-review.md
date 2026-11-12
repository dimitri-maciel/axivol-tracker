# Revisar y editar una copia de la máscara PRE

Una máscara que parece demasiado amplia merece revisión, pero su apariencia no basta para concluir que está equivocada. Axivol permite distinguir **cómo se muestra la máscara** de **qué vóxeles contiene** y editar una copia conservando la publicada.

## Qué representa `Mass`

En el DICOM SEG público de **HCC_002**, el segmento **2** tiene etiqueta `Mass`, descripción `Tumor` y método `SEMIAUTOMATIC`, con nombre `Amira`. Las otras etiquetas son `Liver`, `Portal vein` y `Abdominal aorta`; no hay una etiqueta independiente de tumor viable.

El artículo describe segmentación de tumor, incluyendo componentes viables y necróticos, realizada por tres residentes, revisada por un radiólogo experto y combinada mediante STAPLE. Es coherente interpretar `Mass` como máscara tumoral global; **no como delimitación exclusiva del tejido viable**. STAPLE combina anotaciones: no garantiza cada borde ni significa simplemente unir todas las regiones. [Métodos del dataset](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/).

La comprobación local de HCC_002 encontró 109 cortes referenciados por adquisición y 42 con máscara no vacía: índices internos 53–94, equivalentes a cortes **54–95** en la interfaz. El segmento contiene 324 430 vóxeles. Referenciar 109 cortes no significa que el tumor ocupe todos ellos. Las dos adquisiciones PRE disponibles se comprobaron por separado; siempre debe registrarse cuál se revisó.

## Revisar antes de modificar

1. Comprueba **caso, serie, adquisición y segmento**. Seleccionar `Liver` cuando se quiere revisar `Mass` cambia completamente el objetivo.
2. Alterna imagen sola y superposición. Ajusta Window y Level para observar los tejidos; esos controles no cambian la máscara.
3. Recorre cortes contiguos, incluyendo el principio y final de la región. Una captura aislada no permite evaluar un objeto tridimensional.
4. Examina el borde con contorno y relleno tenue. Distingue zonas cubiertas por el color de zonas realmente añadidas a la máscara.
5. Si persiste una discrepancia, registra el corte y el motivo. Determinar si una zona pertenece al objetivo requiere revisión anatómica; no debe resolverse encogiendo automáticamente el contorno.

Los autores prepararon las anotaciones sobre imágenes PRE en fase portovenosa y verificaron correspondencia de archivos y superposiciones. La etiqueta de una serie o su orden temporal no sustituye revisar adquisición y fase. [Preparación y validación técnica](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/).

## Qué cambia en la presentación

El relleno anterior mezclaba color con la TC a una opacidad del **45 %**, lo que podía ocultar detalle. La nueva presentación combina un **contorno interior de un píxel de imagen** con relleno al **15 %**, ajustable.

El contorno marca píxeles del límite que ya pertenecen a la máscara. No suaviza, dilata ni desplaza sus límites. Con zoom, un píxel de imagen puede ocupar varios píxeles de pantalla. Cambiar opacidad o mostrar solamente el contorno no modifica el conteo ni el volumen. La comprobación de la presentación anterior tampoco encontró color añadido fuera de los píxeles de la máscara.

## Editar una copia

La edición comienza desde la **máscara original del segmento seleccionado**:

- **Paint** añade píxeles con el pincel; **Erase** los retira.
- **Brush radius (mm)** controla el radio físico, inicialmente 3 mm; admite valores mayores que cero y hasta 20 mm. Un radio de 3 mm corresponde a un diámetro de 6 mm.
- Cada trazo afecta únicamente al corte donde se dibuja. No se propaga a los cortes vecinos.
- **Undo** revierte el último trazo. **Clear edits** elimina los cambios del borrador y recupera la copia inicial. **Pause editing** o cerrar el panel muestra de nuevo la máscara publicada y conserva los trazos pendientes para esa selección; Paint o Erase retoman el borrador. Cambiar caso, serie, adquisición o segmento elimina ese borrador.
- Guardar crea una versión nueva e inmutable en `/annotations`. No sobrescribe el SEG original ni una versión anterior.

Las versiones guardadas se pueden previsualizar. Una nueva sesión de edición parte otra vez del original; esta versión no permite continuar pintando sobre una edición guardada. Revisa también los cortes vecinos después de pintar para detectar cambios bruscos entre planos.

Este pincel es **anotación manual**: actúa sobre cualquier píxel dentro de los límites de la imagen, sin filtro de HU ni exclusión automática de padding. No reconoce tejidos. La persona debe evitar pintar zonas ajenas al objetivo o relleno del archivo.

## Cómo se calcula el pincel

Los puntos del trazo son centros de píxel `points = [[y, x], ...]`, con índices enteros desde cero; `slice` también empieza en cero. El servidor recibe `radius_mm`. Con espaciamiento `dy` entre filas y `dx` entre columnas, usamos coordenadas físicas del plano:

$$
p(y,x)=(y\,dy,\;x\,dx).
$$

Para cada tramo del trazo, de `a` a `b`, se busca el punto más cercano:

$$
t=\operatorname{clip}\left(\frac{(p-a)\cdot(b-a)}{\|b-a\|^2},0,1\right),
\qquad q=a+t(b-a).
$$

Se pinta o borra cuando `‖p−q‖ ≤ radius_mm`. Si el tramo tiene longitud cero, se usa la distancia al punto. El trazo completo reúne los píxeles alcanzados por sus tramos, siempre recortados a la imagen.

Así, un pincel circular en milímetros puede parecer elíptico en índices si `dx` y `dy` difieren. Su tamaño no depende del zoom del navegador.

## Cómo cambia el volumen

Comparamos la máscara editada `E` con la original `O`:

$$
N_{añadidos}=|E\setminus O|,\quad
N_{retirados}=|O\setminus E|,\quad
\Delta V=\frac{(N_{añadidos}-N_{retirados})\,dy\,dx\,dz}{1000}\;\mathrm{cm}^3.
$$

Se cuentan diferencias finales, no pasadas del pincel: pintar dos veces el mismo píxel no lo suma dos veces. **Ejemplo sintético:** añadir 200 y retirar 50 vóxeles de 1 mm³ aumenta el volumen 0.15 cm³. No es un resultado de HCC_002 ni un cambio por tratamiento.

## Procedencia, archivos y límites

La edición usa `CandidateStore`, con máscara binaria NPZ y metadatos JSON, estado **`unreviewed`** y etapa **`PRE`**. Se conservan la identificación y hash del SEG fuente, segmento y etiqueta, adquisición, geometría y operaciones aplicadas. El original conserva sus bytes y su hash.

Antes de guardar se releen las cabeceras CT y SEG para comprobar que siguen coincidiendo con la adquisición seleccionada. Se comparan además los hashes de archivos antes y después de aplicar los trazos. Una fuente reemplazada con referencias o geometría distintas produce un error explícito, sin modificar las cachés del visor ni crear una versión incoherente.

| Interfaz técnica | Función |
| --- | --- |
| `POST /api/pre-edits` | Guardar una nueva edición derivada del segmento original. |
| `GET /api/series/{UID}/mask` | Obtener un corte de máscara binaria PNG para el editor. |
| `GET /api/pre-edits` | Recuperar versiones del caso, adquisición y segmento seleccionado. |
| `GET /api/candidates/{id}/export` | Descargar ZIP con NPZ y JSON; no es una exportación DICOM SEG o NIfTI. |

El reporte mantiene `pre_segmentation_measurement` para la máscara publicada y `pre_edited_candidate` para la edición. La comparación POST y los cálculos de respuesta no se habilitan por guardar una corrección PRE.

Una edición manual sin revisión no se convierte en referencia experta. Para usarla como tal hará falta definir el objetivo, identificar al revisor, registrar su criterio y resolver discrepancias. La máscara publicada debe permanecer disponible para contrastar cualquier modificación.

## Comprobar la implementación

Desde `hcc-tace-lab`, con HCC_002 descargado:

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

`test_mask_editing.py` comprueba distancias físicas, espaciado anisotrópico, extremos, borrado, reproducción y límites. `test_pre_mask.py` verifica contorno y transparencia, original intacto, PNG binario, volumen, ZIP y separación PRE/POST. `test_pre_sources.py` sustituye cabeceras sintéticas para asegurar su rechazo. `test_candidates.py` incluye transporte HTTP y filtrado de versiones antes del límite de resultados.

Con Playwright instalado, `AXIVOL_URL=http://127.0.0.1:8771 node tests/ui_pre_mask.cjs` comprueba controles, pincel, versiones y exportación en el navegador. El puerto debe apuntar a una instancia de QA con almacenamiento de anotaciones temporal: esta prueba pinta y borra puntos elegidos para verificar el programa, no para corregir anatomía. Compara la vista previa y el PNG guardado píxel por píxel. Ninguna de estas pruebas demuestra precisión clínica.
