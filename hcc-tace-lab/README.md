# Ejecutar Axivol Tracker

Ejecuta estos comandos desde esta carpeta. Se necesita Docker Desktop y espacio disponible para la colección de 28.57 GB más la extracción temporal. Docker publica el visor en `127.0.0.1:8770` y el contenedor de descarga solo escribe en `data/`.

```sh
mkdir -p data
export HCC_UID=$(id -u) HCC_GID=$(id -g)
docker compose run --build --rm ingest catalog
docker compose run --rm ingest fetch --case HCC_002 --workers 3
docker compose run --rm ingest clinical
docker compose run --rm --entrypoint python ingest -m hcclab.model_assets fetch
docker compose up --build -d app
```

Abre [HCC_002](http://127.0.0.1:8770/?case=HCC_002). La interfaz está en inglés. **Study details** permite elegir la serie y adquisición de cada visor; **Segmentation** selecciona la estructura y su superposición. **Window** y **Level** ajustan la visualización. **Calculate volume** mide la segmentación seleccionada; **Export report** descarga un JSON con sus referencias.

Haz clic en una imagen para recorrer sus cortes con la rueda o las flechas del teclado. Los botones de zoom permiten ampliar y arrastrar la imagen. **Link scrolling** vincula los recorridos por posición relativa, sin afirmar alineación anatómica. **Reset display** restaura zoom y ventana/nivel. **Export image** descarga una captura PNG.

## Revisar y corregir una copia PRE

En **Segmentation**, selecciona `Mass` para revisar la región tumoral publicada. El contorno con relleno inicial al 15 % permite ver mejor la TC; puedes usar solo el contorno o ajustar la opacidad. Estos ajustes no cambian los vóxeles ni el volumen.

Abre **Edit copy** para usar **Paint** o **Erase**, con un radio de pincel en milímetros. Los trazos afectan solo al corte actual. **Undo** revierte el último trazo; **Clear edits** recupera el original. **Save edited copy** crea una versión independiente con estado **Unreviewed**. El selector permite volver a la segmentación publicada o previsualizar una copia guardada; cada nueva sesión de edición comienza desde el original. El ZIP incluye máscara y operaciones reproducibles.

La medición publicada permanece separada de la edición en el reporte (`pre_segmentation_measurement` y `pre_edited_candidate`). Un borde más visible o una corrección manual no demuestran mayor precisión anatómica. Consulta la [guía PRE: significado de Mass, procedimiento, matemáticas y procedencia](docs/pre-mask-review.md).

## Ajustar el borde automáticamente

En **Segmentation**, elige **Boundary band** (1–5 mm, inicialmente 3) y pulsa **Refine boundary**. Se genera una candidata 3D desde el SEG publicado, utilizando las intensidades de esa TC y sus vecinos. Solo puede modificar la franja definida; conserva el núcleo y el exterior. No utiliza la imagen POST ni remuestrea los datos.

El selector **Mask version** permite alternar **Original (published)** y **Refined** en el mismo corte. La ficha muestra los vóxeles añadidos y retirados, los cortes modificados y los parámetros usados. Compara las propuestas con la TC y revisa los avisos. Con contraste insuficiente puede devolver la máscara sin cambios. Todas las salidas son **Unreviewed**; un ajuste no prueba mayor precisión.

Guarda o limpia los trazos manuales antes de refinar. El refinamiento y las nuevas sesiones de pincel parten del original; esta versión todavía no encadena correcciones manuales sobre la candidata refinada. Consulta la [explicación completa del algoritmo, sus límites y la evaluación pendiente](docs/automatic-refinement.md).

## Generar una máscara POST automáticamente

**Estado del código, 13 de noviembre de 2026:** versión `2.1.0`, protocolo `liver_context_medsam2_boundary`. Añade refinamiento 3D del borde POST en una banda fija de 2 mm. **Desplegada y comprobada:** 211/211 pruebas de backend, generación real de HCC_006 y siete grupos de comprobaciones de navegador en HCC_002/HCC_006. Se mantiene pendiente la validación anatómica independiente. Para actualizar otra instalación usa `docker compose up --build -d app`. Las 165/165 pruebas y comprobaciones UI previas de HCC_002/HCC_006 corresponden a `2.0.0`; no se trasladan a esta revisión ni constituyen validación anatómica.

Selecciona las adquisiciones PRE y POST en **Study details**, y el segmento publicado `Mass` en **Segmentation**. Abre **Follow-up mask**, comprueba la referencia **Baseline reference** y pulsa **Generate automatically**. Se exige una única etiqueta `Liver` publicada y no vacía para el contexto anatómico. El proceso usa los segmentos originales incluso si el visor PRE muestra una copia editada o refinada.

Si ya tenías la aplicación instalada, descarga primero el modelo con `docker compose run --build --rm --entrypoint python ingest -m hcclab.model_assets fetch`. El checkpoint fijado ocupa unos 72 MB y se verifica antes de utilizarse. Queda en `data/models`, fuera de Git y del contexto de construcción. La inferencia utiliza Efficient MedSAM2 tiny y corre localmente en CPU. Los ensayos de desarrollo tardaron unos 51–56 segundos por variante, sin contar toda la lectura y registro; no es una garantía de latencia. Los visores y la generación utilizan operaciones separadas.

Primero se estima el registro rígido global PRE → POST y después se refina con `Liver ∪ Mass` PRE como contexto para la información mutua. La transformación final localiza el centro y la caja de `Mass`. El predictor recibe exclusivamente la TC POST en un campo XY de lado físico `max(160 mm, 2 × mayor lado de la caja)`, limitado por la cobertura adquirida. Procesa **todos los cortes z POST**; un corte de grafo ajusta después el borde en la retícula nativa usando sus logits y diferencias de HU entre vecinos. La banda de 2 mm limita dónde puede cambiar etiquetas; no garantiza un error anatómico de 2 mm. PRE aporta localización, nunca una máscara de salida ni un volumen objetivo. Véase el [método de refinamiento POST](docs/research/post-boundary-refinement.md).

El exterior del campo XY queda registrado como **no evaluado**. Tocar un borde artificial con la máscara o con su banda editable produce abstención. También se rechaza una predicción si más del 50 % queda fuera de la ROI de búsqueda; ese control examina la máscara completa, no la recorta. Los diagnósticos distinguen conteos antes y después del ajuste; logits y energía no son confianza clínica calibrada.

La app muestra la candidata en POST y permite recorrer cortes, ocultar la superposición y exportar máscara y procedencia. Si falla un control, presenta el motivo sin guardar una máscara sustitutiva; una candidata anterior permanece intacta. La predicción histórica fallida de HCC_006 está preservada aparte para auditoría, fuera de las propuestas activas. Cambiar de fuente durante el cálculo evita aplicar el resultado a otra adquisición. Los reportes registran los dos segmentos originales, hashes CT/SEG, ambos registros, campo de inferencia, pesos, versiones y código.

**Automatic candidate · Unreviewed** significa propuesta para revisar. El registro calculado tampoco está verificado anatómicamente. No se interpreta como tumor viable ni se habilita un porcentaje de respuesta. Consulta la [guía completa de generación automática POST](docs/automatic-post-segmentation.md): geometría, ecuaciones, parámetros, límites, fuentes, código y evaluación pendiente.

Para estudiar las decisiones y reproducir los ensayos están disponibles el [índice de investigación](docs/research/README.md), las [matemáticas del seguimiento](docs/research/post-mathematics.md), los [runners y sus comandos](experiments/README.md), el [registro histórico de resultados y fallos](experiments/ABLATION.md) y la [arquitectura actual](experiments/ARCHITECTURE.md). Los runners leen TC/SEG originales y guardan resultados fuera del almacén de candidatas; no necesitan una máscara anterior de la app.

## Revisar la máscara POST por cortes y en 3D

En **Segmentation → Follow-up**, selecciona una máscara guardada y pulsa **3D view**. Arrastra para girar, usa la rueda o los botones para acercar y **Reset view** para recuperar la cámara. La vista se carga solo al abrirla; no añade trabajo al recorrido habitual de cortes. Conserva el tamaño físico, todas las componentes y los escalones de la máscara nativa. No cambia la segmentación ni su estado **Unreviewed**.

La [revisión de HCC_006 por cortes](docs/research/hcc006-slice-review.md) documenta los 16 planos ocupados y sus vecinos, con descriptores de los 103 cortes. Identifica una extensión gris de pertenencia incierta, una discontinuidad entre los cortes 55–56 del visor y pequeñas componentes y huecos. La [guía de la superficie 3D](docs/research/lesion-surface.md) explica geometría, controles, límites y pruebas. Ninguna de estas comprobaciones sustituye la referencia anatómica independiente.

La [investigación de los cortes 55–56](docs/research/post-prompt-conditioning.md) compara el condicionamiento inicial, la política de seguimiento y la preparación de la entrada con una localización congelada. Cada comparación exige reproducir exactamente la máscara anterior y conserva resultados, diferencias por corte y fuentes verificadas. Las variantes experimentales no sustituyen automáticamente la candidata guardada.

El [comparador nnInteractive 3D](docs/research/post-neural-refinement.md) prueba otro enfoque: refinar la predicción POST completa con la TC POST nativa, sin puntos manuales ni volver a generar el baseline. Incluye un entorno Docker de investigación separado, pesos verificados y comparaciones antes/después de HCC_006 y HCC_002. Sus resultados siguen siendo experimentales y no sustituyen la máscara activa de la app.

El [comparador MedSAM por corte](docs/research/post-medsam-slice-refinement.md) estudia un ajuste sin propagación temporal. Obtiene cajas automáticamente de la máscara POST congelada y sus vecinos; conserva explícitamente los planos no evaluados. La guía documenta las comparaciones y los controles adicionales que no pudieron prepararse, sin atribuirles una validación satisfactoria.

## Generar una máscara POST con región y semilla

Actualiza el contenedor con `docker compose up --build -d app`. Debajo de los visores encontrarás **Follow-up mask**:

1. Abre la serie y adquisición POST que quieras estudiar. Revisa su fase y cobertura; la selección cronológica no las verifica.
2. Usa **Draw region** para delimitar una caja sobre la imagen y elige el primer y último corte que abarcará. También puedes introducir las coordenadas numéricas.
3. Usa **Pick seed** para colocar un punto dentro de la región. La lectura **Seed HU** ayuda a elegir los límites de intensidad; no identifica tejido por sí sola.
4. Introduce ambos límites HU y pulsa **Generate candidate**. El método conserva únicamente los vóxeles conectados con la semilla dentro de la caja y del intervalo.
5. Recorre los cortes con y sin superposición. Si el borde es incorrecto, ajusta la región, semilla o umbrales y genera otra versión. El pincel PRE todavía no se aplica a los candidatos POST.
6. Exporta el candidato como ZIP: `mask.npz` contiene la máscara booleana en orden `[z,y,x]`; `metadata.json` conserva parámetros, geometría, referencias DICOM y huellas de los archivos fuente. No es todavía un DICOM SEG ni un NIfTI.

Las máscaras se guardan en el volumen Docker `hcc-tace-lab_annotations`, montado en `/annotations`. Los DICOM de `/data` siguen en modo de solo lectura. Reiniciar o reconstruir la app conserva los candidatos; `docker compose down -v` elimina los volúmenes, por lo que no debe usarse si se desean conservar. Usa la exportación para guardar una copia independiente.

El volumen mostrado corresponde a la **región candidata**, con estado **Unreviewed**. No es una referencia clínica ni una medida de tumor viable. `post_candidate` aparece separado en el reporte; `post_segmentation_measurement` y `volume_change_percent` continúan en `null`.

Consulta la [guía de método, uso y evaluación](docs/post-segmentation.md) y la [explicación de matemáticas y código](docs/post-segmentation-math.md).

Las cargas de cortes se agrupan para priorizar la última posición solicitada. Una caché acotada y la precarga de cortes cercanos reducen las esperas. La [guía de interfaz](docs/interface-design.md) documenta los criterios de diseño, accesibilidad y comprobaciones de navegador.

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
