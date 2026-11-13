# Evidencia para segmentar la lesión después de TACE

Fecha de revisión: 12 de noviembre de 2026. Estado: investigación de métodos y decisiones de desarrollo; **no validación clínica**.

Esta revisión responde a una pregunta concreta: ¿cómo pasar de una lesión identificada antes del tratamiento a una candidata de esa misma lesión en la TC posterior, sin confundirla con otro órgano? Complementa [el método implementado](../automatic-post-segmentation.md) y [el protocolo de validación propuesto](post-tace-validation.md). El protocolo **2.0.0**, ya desplegado localmente, incorpora registro global, refinamiento con `Liver ∪ Mass` PRE y contexto XY POST con mínimo solicitado de 160 mm. Las demás variantes y modelos se conservan como experimentos separados; incorporar una decisión al código no demuestra mejor exactitud anatómica.

## 1. Decisión principal

La contribución defendible para la tesis es **localizar la lesión y proponer su contorno POST, con controles de identidad y una evaluación independiente**. No basta con producir una máscara conectada o de aspecto regular. Tampoco basta con que un modelo reconocido genere una salida.

Las comparaciones de contexto completo/local y otros modelos ya aportaron evidencia de sensibilidad y fallos. Las prioridades siguientes son separar el efecto del registro, prompt y campo, comprobar identidad anatómica y construir referencias POST. El protocolo detalla las ablaciones; ninguna constituye por sí sola una garantía de exactitud.

La prioridad científica paralela es obtener referencias POST trazadas por especialistas. Los casos HCC_002 y HCC_006 ya han influido en decisiones de desarrollo: no podrán presentarse después como una prueba independiente.

## 2. Qué queremos delimitar

Hay tres objetos diferentes:

| Objeto | Qué representa | Consecuencia para Axivol |
| --- | --- | --- |
| Lesión completa | Contorno del tumor o de la lesión tratada, incluyendo sus componentes viables y necróticos cuando pueden delimitarse. | Es el objetivo inicial, coherente con la etiqueta PRE `Mass` del dataset. Su límite POST requiere una definición acordada con un especialista. |
| Componente viable | Tejido que conserva un patrón de realce compatible con tumor viable, evaluado con las fases apropiadas. | No se obtiene simplemente restando vóxeles oscuros ni calculando el volumen total. Es otro problema de anotación y validación. |
| Depósito de Lipiodol | Material radiopaco depositado durante determinados procedimientos de TACE. | Una región muy brillante puede corresponder al depósito; segmentarlo no equivale a segmentar todo el tumor ni todo su componente viable. |

El artículo de HCC-TACE-Seg describe segmentación del tumor viable y necrótico conjuntamente en imágenes PRE. mRECIST, en cambio, utiliza el realce arterial para evaluar la porción viable. Por ello, esta aplicación no convierte el volumen de `Mass` en una categoría de respuesta terapéutica. [Moawad et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/), [Lencioni y Llovet](https://pmc.ncbi.nlm.nih.gov/articles/PMC12268942/).

El contraste intravenoso también cambia con la fase de adquisición. Una TC sin contraste, una arterial y una portal del mismo paciente no son imágenes intercambiables. La presencia de Lipiodol puede ocultar o imitar hallazgos en TC convencional; el estudio de TC espectral de Xu y colaboradores ilustra esa dificultad, pero no valida un umbral HU para nuestra tarea. [Xu et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC4691528/).

## 3. Cómo se realizó esta revisión

Se hizo una búsqueda focalizada de fuentes primarias, no una revisión sistemática ni un metaanálisis. Se consultaron artículos completos, páginas oficiales de los datasets y repositorios de los autores. Las familias de búsqueda incluyeron `post-TACE segmentation public dataset`, `longitudinal liver lesion tracking`, `SAM2 medical over-propagation`, `reverse propagation` y `segmentation metrics`.

Se priorizaron: definición del objetivo anatómico, disponibilidad real de etiquetas POST, método de correspondencia longitudinal, dependencia de intervención humana, comportamiento fuera de dominio y evaluación reproducible. Se descartó usar blogs comerciales o cifras de marketing como evidencia del método. Las consultas no incluyeron imágenes ni datos de los estudios locales.

Los DOI de los artículos de la matriz se contrastaron con la API pública de Crossref; las entradas sin DOI editorial se identifican por PMLR o arXiv. La lectura de SLM-SAM2 corresponde a la versión de autor `2505.01854v1`; la identidad de su publicación final se verificó por DOI. Esta búsqueda no permite afirmar que no exista ningún otro dataset POST en el mundo.

## 4. Matriz crítica de fuentes

Las cifras de muestra describen las publicaciones; no son resultados de Axivol. Las decisiones de la última columna son propuestas nuestras.

| Fuente primaria y alcance | Evidencia metodológica relevante | Qué podemos trasladar y qué no |
| --- | --- | --- |
| **1. Moawad et al., 2023 — HCC-TACE-Seg.** 105 pacientes con TC antes y después de TACE. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/); [DOI](https://doi.org/10.1038/s41597-023-01928-3). | Los DICOM-SEG publicados corresponden a PRE. Las anotaciones incluyen tumor global y se construyeron con revisión experta y combinación STAPLE. | Base longitudinal útil, con procedencia verificable. No ofrece una referencia volumétrica POST equivalente; las tablas RECIST/mRECIST no sustituyen esa máscara. |
| **2. Lencioni y Llovet, 2010 — mRECIST.** Propuesta metodológica de evaluación de respuesta. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC12268942/); [DOI](https://doi.org/10.1055/s-0030-1247132). | Distingue diámetro de tejido viable realzante de dimensiones anatómicas globales. | Define por qué viabilidad y volumen global requieren objetivos distintos. No es un algoritmo automático de segmentación ni una validación de nuestro visor. |
| **3. Xu et al., 2015 — TC espectral tras TACE.** Estudio preliminar de 10 pacientes. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC4691528/); [DOI](https://doi.org/10.5812/iranjradiol.26009). | Compara información convencional y descomposición de materiales ante depósito de Lipiodol y lesión residual. | Justifica registrar fase y artefactos. No autoriza inferir viabilidad desde una ventana fija de TC convencional. |
| **4. Bartnik et al., 2024 — WAW-TACE.** 233 pacientes, 377 máscaras tumorales. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC11605144/); [DOI](https://doi.org/10.1148/ryai.240296). | Contiene imágenes basales multifásicas. Las lesiones se corrigieron y revisaron por radiólogos; las máscaras de órganos automáticas son un producto diferente. | Puede ayudar a estudiar segmentación PRE y variación de fase. No aporta TC POST. La máscara tumoral pertenece a una fase específica, no automáticamente a todas. |
| **5. Malpani et al., 2022 — Lipiodol en CBCT.** 36 pacientes; U-Net y comparador por intensidad. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC8972393/); [DOI](https://doi.org/10.1016/j.jvir.2021.12.017). | Usa segmentaciones de depósito de Lipiodol revisadas por un radiólogo y procesamiento centrado en hígado. | Precedente de segmentación aprendida y contexto anatómico. Cambian modalidad, objetivo y momento: CBCT intraprocedural y depósito no equivalen a lesión global en TC POST. |
| **6. Hering et al., 2021 — seguimiento de lesiones.** Cohorte de melanoma, con 43 pacientes en prueba. [Texto completo PMLR](https://proceedings.mlr.press/v143/hering21a/hering21a.pdf). | Separa registro, región local de búsqueda, segmentación en seguimiento y selección de la lesión correspondiente. Incluye lesiones desaparecidas. | Arquitectura transferible como hipótesis. Sus márgenes y distancias de aceptación no deben copiarse como límites clínicos para HCC. La máscara propagada no se considera un contorno final exacto. |
| **7. Zhou et al., publicación electrónica 2024 — detect-then-track.** Cáncer hepático tratado con inmunoterapia. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC11811366/); [DOI](https://doi.org/10.1007/s10278-024-01132-8). | Detección y registro con separación por paciente. Las referencias longitudinales son diámetros, cajas y límites superior/inferior. | Útil para separar detección, identidad y medición. Las pseudomáscaras derivadas de diámetros no son contornos 3D independientes; las cohortes longitudinales internas no se distribuyen públicamente. |
| **8. Rokuss et al., 2025 — LesionLocator.** Segmentación 3D y seguimiento con prompts. [Artículo CVPR](https://openaccess.thecvf.com/content/CVPR2025/html/Rokuss_LesionLocator_Zero-Shot_Universal_Tumor_Segmentation_and_Tracking_in_3D_Whole-Body_CVPR_2025_paper.html); [DOI](https://doi.org/10.1109/CVPR52734.2025.02875). | Combina modelo 3D, registro y datos longitudinales reales de melanoma y sintéticos. Evalúa recuperación de identidad además de solapamiento. | Comparador especializado potencial, sujeto a recursos y auditoría del checkpoint. La distribución pública de seguimientos sintéticos no constituye POST HCC real con referencia experta. |
| **9. Ma et al., 2025 — MedSAM2.** Modelo generalista con propagación entre cortes. [Preprint completo](https://arxiv.org/html/2504.03600v1); [código oficial](https://github.com/bowang-lab/MedSAM2). | El protocolo CT descrito corrige la máscara inicial y especifica los extremos de la lesión; reconoce límites de memoria y continuidad 3D. | Sirve como propuesta inicial. No demuestra segmentación autónoma de HCC post-TACE a lo largo de toda una TC. Sus resultados no se transfieren al checkpoint EfficientMedSAM2 tiny usado aquí. |
| **10. Bai et al., 2024 — RevSAM2.** Segmentación con imágenes de soporte etiquetadas. [Preprint completo, v2](https://arxiv.org/html/2409.04298v2); [registro arXiv](https://arxiv.org/abs/2409.04298). | Propaga hacia una imagen y de vuelta al soporte; compara el retorno con sus etiquetas y selecciona anclas. | Inspira una prueba de consistencia. Con una máscara inicial automática, concordar consigo mismo no demuestra identificar la lesión correcta. No hay validación HCC post-TACE en este trabajo. |
| **11. Chen et al., 2026 — SLM-SAM2.** Publicación electrónica 2025; memoria corta y larga. [Versión de autor](https://arxiv.org/html/2505.01854v1); [DOI](https://doi.org/10.1109/TMI.2025.3627954). | Estudia sobrepropagación y aprende una fusión de memorias usando volúmenes anotados de órganos, huesos y músculos. | Apoya investigar deriva de seguimiento. Requiere entrenamiento; reducir la memoria de nuestro modelo no reproduce su método. El repositorio oficial usa GPLv3 y no ofrece en su guía un modelo POST HCC validado. |
| **12. Wasserthal et al., 2023 — TotalSegmentator.** 104 estructuras anatómicas. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC10546353/); [DOI](https://doi.org/10.1148/ryai.230024). | Modelos y anotaciones de órganos en TC con revisión manual y separación de pacientes. | Hígado/riñones pueden dar contexto o detectar contradicciones anatómicas. Una máscara de hígado no es una máscara de tumor; su error debe analizarse por separado, especialmente ante lesiones periféricas. |
| **13. Manghi et al., 2025 — segmentación HCC en TC.** Compara nnU-Net, Swin UNETR y U-Mamba. [Texto](https://www.nature.com/articles/s41598-025-26019-8); [DOI](https://doi.org/10.1038/s41598-025-26019-8). | Evalúa datasets HCC y aporta HCC-ARSeg, con anotaciones multifásicas revisadas; disponibilidad bajo solicitud. | Comparador futuro para contorno tumoral 3D. No establece una cohorte pública emparejada antes/después de TACE. «Antes y después de contraste» no significa «antes y después de tratamiento». |
| **14. Taha y Hanbury, 2015 — métricas 3D.** Análisis metodológico. [Texto](https://pmc.ncbi.nlm.nih.gov/articles/PMC4533825/); [DOI](https://doi.org/10.1186/s12880-015-0068-x). | Distingue solapamiento, volumen y distancias; muestra que distintas métricas responden a errores diferentes. | Combinar Dice con error superficial y volumétrico. No interpretar un buen volumen como buen contorno ni un buen contorno como identidad correcta. |
| **15. Maier-Hein et al., 2024 — Metrics Reloaded.** Marco de consenso para validación. [Texto](https://www.nature.com/articles/s41592-023-02151-z); [DOI](https://doi.org/10.1038/s41592-023-02151-z). | Selecciona métricas según objetivo, estructura y jerarquía de datos; advierte sobre agregaciones inadecuadas. | Evaluar por paciente/lesión y reportar fallos y abstenciones. No obtener una aparente muestra grande tratando los cortes del mismo paciente como observaciones independientes. |
| **16. Nicoli et al., 2026 — segmentación de segmentos y lesiones hepáticas en TC/RM.** Publicación electrónica el 24 de octubre de 2025. [Texto completo](https://link.springer.com/article/10.1007/s10278-025-01716-y); [DOI](https://doi.org/10.1007/s10278-025-01716-y). | Desarrolla modelos nnU-Net y explora su uso longitudinal en TACE: **172 estudios TC válidos**, no 172 pacientes. Incluye TC hasta 6 meses antes y 12 meses después; prefiere fase arterial tardía y sustituye por portal si falta. Un POST puede reutilizarse como PRE de otro procedimiento. | Precedente pertinente para detectar/segmentar lesiones directamente en POST y evaluar la correspondencia por separado. La demostración TACE no proporciona una descarga de referencias POST expertas. No trasladar sus métricas a HCC-TACE-Seg ni a otra versión de pesos de TotalSegmentator; auditar datos de entrenamiento, checkpoint, licencia y fase por separado. |

## 5. Disponibilidad real de referencias POST

No se identificó en las fuentes revisadas una descarga de máscaras expertas 3D POST HCC-TACE equivalente a `Mass`. Esto no prueba su inexistencia global. La matriz distingue PRE, etiquetas derivadas, cohortes privadas y seguimiento sintético.

La [ficha TCIA](https://www.cancerimagingarchive.net/collection/hcc-tace-seg/) permite descargar CT/SEG con CC BY 4.0; sus recuentos actuales de series difieren de los del artículo, por lo que hay que fijar un manifiesto de archivos. [WAW-TACE en Zenodo](https://zenodo.org/records/12741586) identifica la fase específica de cada máscara tumoral: respiración y posición pueden variar entre fases. Ninguno aporta la referencia POST necesaria.

Nicoli y colaboradores aportan un antecedente directamente relacionado con TACE, pero su sección de disponibilidad anuncia **el modelo en GitHub**, no las imágenes y anotaciones longitudinales individuales. Su cohorte TACE utiliza 172 TC tras excluir estudios incompletos o irrelevantes de 232 recuperados. Repetir pacientes, reutilizar un POST como PRE y combinar fases exige conservar la unidad paciente/procedimiento; no son 172 observaciones independientes equivalentes. El artículo estudia TC y RM, mientras que la identidad y el entrenamiento de cada artefacto descargable deben verificarse por su propia versión. El nombre TotalSegmentator o una licencia abierta del artículo no prueban que los pesos actuales reproduzcan ese modelo ni que compartan licencia. [Métodos y disponibilidad de Nicoli et al.](https://link.springer.com/article/10.1007/s10278-025-01716-y).

## 6. HCC_006: qué cambió y qué sigue sin saberse

El caso de desarrollo usa PRE adquisición 3, segmento 2 `Mass`, y POST adquisición 1. La auditoría de fuentes/geometría no mostró inversión de ejes. La salida inicial presentó una continuación sospechosa hacia otra estructura y fue retirada; [la guía del método](../automatic-post-segmentation.md#fallo-observado-en-hcc_006) conserva los detalles. Ni seleccionar la componente anclada al prompt ni parar ante un indicador negativo de presencia resolvía el problema: la deriva era conectada y el indicador permanecía positivo.

Los siguientes son pilotos históricos, sin referencia experta, que procesaron todos los cortes POST; no son las mediciones de una nueva candidata del protocolo 2:

| Variante | Vóxeles emitidos | Fuera de región de búsqueda |
| --- | ---: | ---: |
| Campo completo, ventana 40/400 HU | 184 806 | 90,88 % |
| Contexto XY mínimo 160 mm, misma ventana | 17 167 | 0 % |
| Contexto 240 mm | 24 492 | 26,72 % |
| Contexto 160 mm, ventana 200/800 HU | 177 974 | 89,79 % |
| Contexto 160 mm, registro refinado con contexto hepático | 11 485 | 0 % |

Procedencia local: `tmp/post-improvement/HCC_006-{full,local160,local240,local160wide,local160liver}.json`; son registros de desarrollo, no referencias. Las imágenes permanecen locales. La [reproducción desde CT/SEG originales](../../experiments/ABLATION.md#reproducción-desde-las-fuentes-originales) recalculó el registro y produjo 11 120 vóxeles con `local160liver`, frente a los 11 485 del piloto con otra localización. La posterior comprobación de interfaz del protocolo 2 generó una candidata distinta de 11 172 vóxeles. No se exige igualdad entre ejecuciones que utilizaron prompts distintos ni se interpreta la diferencia como error anatómico.

**Cero extensión fuera de la región no significa lesión correcta.** Las dos variantes con 0 % pueden elegir objetivos distintos: cambiar registro/prompt o ventana cambia qué tejido sigue la red. Incluir el foco brillante tampoco acredita incluir toda la lesión; puede favorecer Lipiodol y omitir periferia poco contrastada. La identidad del objetivo y sus límites requieren revisión independiente. El umbral de rechazo del 50 % actual fue elegido después del fallo inicial y no es un criterio clínico validado.

Además, el prompt y la región de control provienen del mismo registro: su concordancia es un control interno, no una comprobación independiente de localización. Un error compartido puede hacer pasar una máscara equivocada.

Los [comparadores 3D y anatómicos](post-tace-models.md) refuerzan esta cautela. nnInteractive fue sensible a la caja y produjo una prolongación conectada en una de las salidas; TotalSegmentator anatómico aportó una señal renal coherente con la deriva observada. Su tarea de lesiones no emitió vóxeles positivos dentro de las regiones de búsqueda del ensayo; esto no demuestra ausencia tumoral. Son predicciones con sus propios errores y costes, no referencias expertas ni una votación que elija automáticamente el contorno correcto. Los resultados, pesos, licencias y consumos se documentan en ese informe para evitar mezclarlos con la evidencia publicada.

## 7. Decisiones incorporadas y trabajo pendiente

- **Contexto local incorporado:** el protocolo 2 usa el mismo checkpoint con un campo XY solicitado de al menos 160 mm, ajustado al campo de visión disponible, y todos los cortes POST. El recorte de imagen cambia escala y contenido; no es un recorte cosmético de la máscara. Se registra el mapeo XY nativo y se rechaza contacto con bordes artificiales. No hay expansión automática ni selección por paciente entre las variantes ensayadas.
- **Contexto hepático incorporado al registro:** `Liver ∪ Mass` originales PRE restringen la métrica del refinamiento; no se transfieren como máscara POST. El hígado y riñones predichos por otro modelo siguen siendo comparadores separados. No se borra automáticamente todo lo situado fuera de un hígado predicho: lesiones periféricas y errores de órgano pueden producir falsos rechazos.
- **Consistencia:** un retorno al corte inicial o perturbaciones predefinidas revelan estabilidad, no verdad. Una ancla equivocada puede ser autoconsistente. No escoger por paciente el resultado más agradable entre múltiples pruebas.
- **Comparadores 3D ensayados:** nnInteractive y TotalSegmentator se mantienen fuera de la ruta de generación de la app. No resolvieron la ambigüedad como para tratarlos como un reemplazo validado. Las siguientes comparaciones deben fijar pesos, datos previos, recursos y prompts; cambiar de arquitectura no resuelve la ausencia de referencia. SLM-SAM2 requiere entrenamiento y no se reproduce ajustando un parámetro de memoria.

Los brazos, métricas y criterios de fallo se especifican una sola vez en [el protocolo](post-tace-validation.md#6-brazos-y-ablaciones).

## 8. Decisiones que no se justifican

- Reducir la máscara hasta que parezca del tamaño de PRE: introduce la respuesta esperada en la salida.
- Usar la máscara PRE trasladada como volumen POST o como referencia para calcular Dice.
- Ajustar un umbral HU porque distingue un foco brillante: puede aislar Lipiodol y omitir tejido de la lesión.
- Aplicar un corte arbitrario al logit para corregir solo HCC_006.
- Presentar Dice entre dos ejecuciones del mismo modelo como exactitud anatómica.
- Ocultar abstenciones o reportar solo casos visualmente convincentes.
- Reutilizar métricas publicadas de otra tarea como rendimiento esperado de Axivol.

