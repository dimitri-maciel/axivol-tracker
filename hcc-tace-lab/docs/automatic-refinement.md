# Refinamiento automático de una máscara PRE

## Qué intenta resolver

Esta función propone un ajuste del borde de una máscara PRE ya existente. Su propósito es reducir el trabajo de corrección manual cuando la delimitación inicial está cerca del objetivo. **No detecta el tumor desde cero ni determina qué tejido es viable.** La salida es una candidata que debe compararse con la imagen y revisarse.

Conviene separar tres operaciones:

| Operación | Qué cambia | Qué permanece igual |
| --- | --- | --- |
| Cambiar ventana, nivel, opacidad o contorno | La presentación en pantalla. | Los vóxeles de la máscara y su volumen. |
| Refinar el borde | La pertenencia de ciertos vóxeles a una copia de la máscara. | La TC, su geometría y el SEG publicado. |
| Revisar la anatomía | El juicio sobre qué región debe incluirse. | El algoritmo no sustituye esta decisión. |

Una candidata más lisa o con menor volumen no es necesariamente más correcta. La máscara `Mass` de HCC_002 corresponde al objetivo tumoral global; el artículo del dataset describe tejido viable y necrótico. Buscar únicamente una intensidad uniforme podría recortar tejido que sí debe permanecer incluido. [Artículo de HCC-TACE-Seg](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/).

## Decisión del método

Se elige un **corte de grafo binario en una franja tridimensional limitada**, con información de intensidad, diferencias entre vecinos y preferencia por conservar la máscara inicial. La elección responde a estas restricciones del proyecto:

- Se dispone de una máscara inicial y de la TC que referencia.
- El procesamiento se realiza en CPU, usando NumPy y SciPy.
- Se conserva la retícula nativa y se expresan las distancias en milímetros.
- Hace falta limitar explícitamente dónde puede cambiar la máscara.
- No hay una referencia independiente que permita afirmar que un modelo nuevo mejora la precisión.

Boykov y Jolly formularon la segmentación binaria mediante términos de región y borde, sujetos a semillas de objeto y fondo. El corte mínimo resuelve la energía definida por esos términos. Nuestra franja, las semillas derivadas de la máscara y el prior de conservación son decisiones de adaptación; no se presenta la implementación como una reproducción exacta de sus experimentos. Su artículo usó 26 vecinos en los ejemplos 3D; esta propuesta utiliza vecinos por cara. [Artículo original, PDF de los autores](https://www.csd.uwo.ca/~yboykov/Papers/iccv01.pdf), [DOI verificado](https://doi.org/10.1109/ICCV.2001.937505).

No se descargan pesos ni se entrena una red neuronal. Los histogramas calculados sobre la adquisición seleccionada son estimaciones locales de intensidad, no un modelo aprendido con una cohorte. Por eso no corresponde crear un dossier de pesos o atribuir resultados de modelos preentrenados a esta función.

## Una franja en milímetros

Sea `M` la máscara original, con valores 1 para objeto y 0 para fondo. El volumen se almacena como `[z, y, x]` y su espaciamiento es `s = (dz, dy, dx)`.

Para cada vóxel interior calculamos la distancia al centro del vóxel exterior más cercano; para cada exterior, la distancia al interior más cercano:

$$
d_{in}=\operatorname{EDT}(M;s),\qquad
d_{out}=\operatorname{EDT}(\neg M;s).
$$

`EDT` es la transformada de distancia euclidiana. SciPy permite pasar el espaciamiento de cada eje mediante `sampling`; omitirlo trataría todos los vóxeles como cubos de tamaño uno. Las distancias son entre centros de vóxel en la retícula, no una medición subvoxel de una superficie anatómica. [Documentación de `distance_transform_edt`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html).

Con radio `r`, las regiones quedan definidas así:

$$
F=\{p:M_p=1,\ d_{in}(p)>r\},\qquad
G=\{p:M_p=0,\ d_{out}(p)>r\}.
$$

`F` es el núcleo interior fijo y `G` el fondo fijo. La franja restante contiene los vóxeles que pueden cambiar. El resultado conserva `F` como objeto y `G` como fondo.

El valor inicial es **3 mm**, con rango de **1 a 5 mm**. Es un parámetro de ingeniería que todavía requiere evaluación, no una distancia clínicamente validada. Un radio de 3 mm permite considerar vecinos del eje z cuando su separación es 2.5 mm, como en la adquisición PRE del piloto HCC_002. No cambia el espaciado de esa adquisición a 3 mm ni crea cortes nuevos.

El radio se aplica hacia dentro y hacia fuera. No significa «reducir la lesión 3 mm»: la propuesta puede añadir o retirar vóxeles, o conservar el resultado. Tampoco garantiza que cualquier error quede corregido; los errores que estén dentro del núcleo fijo o fuera de la franja quedan fuera de su alcance.

## Qué evidencia usa cada decisión

La imagen se interpreta en unidades Hounsfield a partir de los píxeles DICOM y su transformación correspondiente. Los valores usados para calcular costes no son los colores de la superposición ni la captura del navegador. Cambiar Window o Level no debe cambiar la candidata.

El modelo de intensidad compara distribuciones del interior y de un entorno exterior próximo. Los histogramas permiten representar varias intensidades frecuentes, algo más flexible que asumir una sola media para todo el tumor. Suavizar esos histogramas evita cambios bruscos entre intervalos y probabilidades exactamente nulas; **no equivale a suavizar la TC ni a borrar detalles de la máscara**.

Un histograma cuenta cuántos vóxeles caen en cada intervalo de intensidad. Aquí se construyen dos: uno con el núcleo interior `F` y otro con un anillo exterior cuya distancia a la máscara está entre `r` y `r + 3 mm`. Se excluye padding de ambas muestras. No se usa todo el fondo de la TC: aire, mesa y tejidos lejanos no representan necesariamente el entorno de la lesión.

Cada histograma tiene 512 intervalos de 8 HU entre −1024 y 3072 HU; los valores fuera de ese rango se asignan a sus extremos. Se aplica un suavizado gaussiano de un intervalo y una pseudoprobabilidad de `10⁻⁶`, seguida de normalización. Esos valores se usan para calcular costes; no reclasifican clínicamente los tejidos ni alteran los HU originales.

Denominamos `P_F(I)` y `P_B(I)` a las verosimilitudes de una intensidad bajo las muestras de objeto y fondo. Son estimaciones de `P(intensidad | clase)`, **no probabilidades clínicas de que un vóxel sea tumor**. Ambas clases pueden contener tejidos distintos o estar mal representadas por las semillas.

La información de borde utiliza las diferencias de intensidad entre vóxeles vecinos. Separar vecinos con intensidades parecidas tiene un coste; separarlos donde hay un cambio grande puede ser más barato. Un cambio de intensidad puede coincidir con un límite anatómico, pero también con un vaso, una zona necrótica, un artefacto o ruido. El grafo no sabe cuál de esas explicaciones es la correcta.

Finalmente, un término de conservación penaliza apartarse de la máscara original. Este prior introduce la suposición explícita de que la anotación publicada es un punto de partida útil. No demuestra que lo sea en cada borde; puede mantener errores de esa anotación.

Antes de optimizar se comprueba si las muestras permiten construir una propuesta. Se requieren al menos 32 vóxeles válidos de cada clase. Además se calcula la distancia de variación total entre los dos histogramas normalizados:

$$
\operatorname{TV}(P_F,P_B)=\frac12\sum_k|P_F(k)-P_B(k)|.
$$

Si `TV < 0.05`, las distribuciones se consideran insuficientemente distintas para este procedimiento y se devuelve una copia intacta. También se conserva el original si faltan muestras, incluido un núcleo vacío. Este control es una decisión conservadora del algoritmo, no una prueba estadística ni un diagnóstico de ausencia de borde.

## Qué significa resolver el grafo

Cada vóxel modificable es un nodo con dos opciones: objeto o fondo. Los costes individuales expresan cuánto concuerda cada opción con la intensidad y con la máscara inicial. Las conexiones entre vecinos expresan el coste de cortar una continuidad local.

Una energía binaria tiene la forma:

$$
E(L)=\sum_p D_p(L_p)
+\sum_{\{p,q\}\in\mathcal N}w_{pq}[L_p\ne L_q],
$$

donde `L` es la nueva asignación, `D` el coste individual y `w` un peso no negativo entre vecinos. El símbolo entre corchetes vale uno cuando la condición es verdadera y cero cuando es falsa. Las etiquetas fijas no forman parte de las decisiones libres.

En esta implementación, `V = dz·dy·dx` es el volumen de un vóxel en mm³. Si `d_p` es su distancia al vóxel más próximo de la clase opuesta en la máscara inicial, el coste individual es:

$$
D_p(l)=V\left[-\log P_l(I_p)
+\left(\frac{d_p}{r}\right)^2[l\ne M_p]\right].
$$

El primer término favorece la clase cuya distribución explica mejor esa intensidad. El segundo penaliza cambiar la etiqueta inicial; su efecto aumenta hacia el límite fijo de la franja. El peso de ese prior es uno en esta versión.

Para vecinos por cara, separados por `s_a` milímetros en el eje correspondiente:

$$
w_{pq}=\ell\,\frac{V}{s_a}\,
\exp\left[-\frac12\left(\frac{I_p-I_q}{\sigma_{HU}}\right)^2\right],
\qquad \ell=0.5\;\mathrm{mm},\quad\sigma_{HU}=20\;\mathrm{HU}.
$$

`V/s_a` es el área de la cara compartida. Así se tiene en cuenta que una cara entre cortes separados 2.5 mm no tiene necesariamente la misma área que una cara entre columnas separadas 0.78125 mm. Esta ponderación no convierte la rejilla de seis vecinos en una superficie perfectamente isotrópica; permanece una aproximación discreta.

Las conexiones a vóxeles fijos se incorporan al coste individual de la opción contraria. Se evita representar las restricciones con una capacidad «infinita» arbitraria. Solo se asignan nodos a vóxeles que pueden cambiar; la máscara completa no se convierte en una matriz densa de conexiones.

La resolución usa flujo máximo/corte mínimo. SciPy exige una matriz dispersa CSR con capacidades enteras; por tanto, los costes reales deben convertirse a una escala entera controlada. Deben registrarse esa escala, los pesos y las restricciones de tamaño, y comprobarse que las capacidades no se desbordan. El resultado es un mínimo de **la energía discreta que realmente se construyó**, incluyendo su cuantización. «Óptimo» no significa «contorno anatómicamente verdadero». [Documentación de `maximum_flow`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.csgraph.maximum_flow.html).

La escala inicial es 256 unidades enteras por unidad de energía. Puede reducirse para mantener la suma total de capacidades por debajo de `10⁹`. Las comprobaciones se realizan en enteros de 64 bits y se entregan capacidades de 32 bits al solver utilizado. Esta reducción puede hacer indistinguibles costes cercanos; la escala efectiva debe quedar registrada.

## Parámetros y estados de esta versión

El núcleo del cálculo es `refine_boundary(volume_hu, original_bool, spacing_zyx, band_mm=3, *, valid_mask=None)`. Devuelve una copia booleana de la máscara y diagnósticos. Los controles de origen DICOM y el guardado corresponden a la capa de aplicación.

| Parámetro o límite | Valor | Interpretación |
| --- | --- | --- |
| Radio de la franja | 3 mm; permitido 1–5 mm | Alcance del cambio, medido sobre centros de vóxel. |
| Muestras mínimas | 32 por clase | Evita estimar histogramas con un conjunto insuficiente. |
| Distancia TV mínima | 0.05 | Por debajo, conserva el original por contraste insuficiente. |
| Escala de contraste entre vecinos | 20 HU | Sensibilidad del coste de borde a diferencias de intensidad. |
| Regularización espacial `ℓ` | 0.5 mm | Intensidad relativa de la penalización por separar vecinos. |
| Peso del prior original | 1 | Preferencia por conservar la etiqueta inicial. |
| Grafo | Máximo 300 000 nodos variables | Límite de trabajo; no cambia automáticamente la resolución. |
| Región recortada | Máximo 4 millones de vóxeles | Límite de memoria y procesamiento local. |
| Volumen de entrada | Máximo 64 millones de vóxeles | Límite de tamaño admitido. |

Todos son parámetros de ingeniería. Ni 20 HU ni TV 0.05 deben interpretarse como umbrales que distingan tumor, necrosis, hígado o tejido viable. No se ha demostrado todavía que esos valores sean óptimos para HCC-TACE-Seg.

Los estados del cálculo distinguen:

- **`refined`:** la candidata contiene cambios; no significa que sean mejoras confirmadas.
- **`unchanged`:** la optimización conserva la máscara.
- **`insufficient_contrast`:** se conserva la máscara porque los histogramas son demasiado parecidos o no hay muestras suficientes; `reason = insufficient_samples` identifica este último caso.

Una máscara original vacía se rechaza. Si contiene vóxeles de objeto sobre padding, se informa el problema: no se inventan intensidades ni se borra esa parte silenciosamente. El padding que era fondo permanece fijo y fuera de muestras y grafo. Superar los límites de tamaño también genera un estado de error, sin remuestrear ni recortar el objetivo para forzar la ejecución.

## Cuándo puede fallar o no aportar una mejora

1. **Tumor heterogéneo.** Una región necrótica puede parecerse al fondo. Mantener el núcleo reduce la posibilidad de eliminar su interior, pero no resuelve automáticamente los márgenes heterogéneos.
2. **Contraste insuficiente.** Lesión e hígado pueden tener distribuciones similares. El algoritmo puede conservar el original o encontrar otro contorno igualmente plausible para su energía.
3. **Vasos y otras estructuras próximas.** Sus bordes pueden ser más marcados que el límite buscado y atraer la propuesta.
4. **Máscara inicial incorrecta.** Las semillas se derivan de ella; un error importante se incorpora como restricción. No hay independencia entre inicialización y resultado.
5. **Detalle fino y anisotropía.** La elección de vecinos y la cuantización de la retícula pueden producir escalones. Una superficie visualmente lisa no equivale a precisión anatómica.
   El corte mínimo tampoco garantiza conservar la topología: puede introducir huecos o componentes pequeños dentro de la franja. Esas zonas requieren revisión; la implementación no aplica una eliminación automática por tamaño.
6. **Borde del campo de visión o datos inválidos.** Falta información fuera de la imagen. El padding no representa tejido y no debe utilizarse para aprender el aspecto del fondo.

Una franja pequeña limita el alcance de estos fallos, pero no los elimina. Si no hay un núcleo utilizable, contexto exterior suficiente o geometría compatible, se debe informar el estado y conservar la máscara publicada. No procede aumentar el radio automáticamente hasta obtener un resultado, retirar todas las regiones pequeñas ni conservar únicamente el componente más grande.

## Operación y archivos

La operación de aplicación es `Lab.create_pre_refinement`. Toma siempre el segmento publicado de la adquisición PRE seleccionada; no usa una máscara POST ni traslada una máscara entre estudios. La lectura cuantitativa verifica que los píxeles correspondan a TC con unidades HU admitidas, y la máscara se alinea mediante las referencias y geometría del SEG.

| Interfaz | Función |
| --- | --- |
| `POST /api/pre-refinements` | Generar y guardar una candidata con `case_id`, `series_uid`, `stack_index`, `segment` y `band_mm`. |
| `GET /api/pre-edits` | Recuperar las candidatas PRE del caso, adquisición y segmento, incluidas las manuales y las de refinamiento. |
| `GET /api/candidates/{id}` | Consultar metadatos y diagnósticos de una candidata. |
| `GET /api/candidates/{id}/export` | Exportar un ZIP con máscara binaria NPZ y metadatos JSON. |

El método se identifica como `boundary_graph_cut`, versión `1.0.0`. Cada ejecución guardada recibe un identificador propio en `/annotations`; la fuente TCIA permanece en `/data`, de solo lectura para la app. También puede conservarse una ejecución cuyo resultado sea idéntico al original: su diagnóstico explica que el método no propuso cambios.

El ZIP no es un DICOM SEG ni un archivo NIfTI. La matriz conserva el orden `z,y,x`; el JSON aporta sus referencias y geometría. Un nombre como `Refined Mass` identifica el procedimiento aplicado; hay que leer `outcome` para saber si hubo cambios y `status = unreviewed` para conocer su estado de revisión.

## Qué se debe revisar y registrar

Antes de ejecutar, comprobar caso, estudio, serie, adquisición y segmento. Después, comparar original y candidata sobre la misma ventana, con contorno y relleno tenue. Revisar cortes vecinos y los extremos del objeto; localizar específicamente los vóxeles añadidos y retirados.

Registrar los parámetros, método y versión, hashes de las fuentes y del código, geometría, conteos original y candidato, vóxeles añadidos y retirados, y duración del procesamiento. La integración relee cabeceras y compara hashes antes y después del cálculo para rechazar fuentes cambiantes. Estos datos permiten reproducir una ejecución; por sí solos no califican su exactitud.

La diferencia de volumen se calcula sobre la retícula original:

$$
\Delta V=\frac{(N_{añadidos}-N_{retirados})\,dz\,dy\,dx}{1000}\;\mathrm{cm}^3.
$$

Es una diferencia entre dos delimitaciones del mismo estudio PRE. No es un cambio por tratamiento ni habilita una comparación PRE–POST. Las candidatas deben conservarse como versiones `unreviewed`, separadas del SEG fuente y de cualquier referencia clínica aprobada.

## Cómo evaluar si ahorra corrección manual

### Comprobación local inicial

Se perfiló **HCC_002, estudio PRE, adquisición 1, segmento 2 (`Mass`)**, en su retícula nativa de 2.5 × 0.78125 × 0.78125 mm. La fuente contiene 324 430 vóxeles. En una ejecución del núcleo, después de cargar HU y máscara:

| Franja | Nodos variables | Tiempo del núcleo | Añadidos | Retirados |
| --- | ---: | ---: | ---: | ---: |
| 3 mm | 164 274 | 0.301 s | 11 934 | 9 810 |
| 5 mm | 272 666 | 0.480 s | 35 740 | 18 199 |

El pico RSS observado del proceso fue de 422 MiB tras la primera ejecución y 501 MiB tras la segunda; es una medición acumulada del proceso, no memoria exclusiva del grafo. Los tiempos no incluyen lectura, verificación de hashes, guardado ni carga del navegador, y no constituyen una garantía de rendimiento. El hash del SEG publicado permaneció idéntico. **Los conteos indican diferencias con la inicialización, no correcciones anatómicas confirmadas.** Una franja mayor permitió más cambios; eso no prueba que sea preferible.

Las pruebas sintéticas incluyen un objeto de borde conocido con máscaras iniciales expandidas y contraídas, intensidades interiores bimodales, HU indistinguibles, espaciado anisotrópico y padding. Un grafo pequeño se compara contra enumeración exhaustiva de todas sus etiquetas, verificando la correspondencia entre corte y energía. Estas pruebas permiten detectar errores de implementación; no reproducen la complejidad clínica de una lesión hepática.

Desde `hcc-tace-lab`, ejecuta la suite con el caso piloto descargado:

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

`tests/ui_refinement.cjs` comprueba la función en un navegador con Playwright. Debe apuntar mediante `AXIVOL_URL` a una instancia de QA con anotaciones temporales: genera versiones para comprobar comparación, exportación, navegación y respuestas tardías. `tests/ui_pre_mask.cjs` verifica que el pincel manual sigue funcionando. Las capturas y reportes de estas ejecuciones se mantienen fuera de Git.

### Validación anatómica pendiente

**Primero, comprobar la ingeniería.** Usar imágenes sintéticas con contornos conocidos para evaluar conservación del núcleo y del exterior, límites físicos de la franja, anisotropía, signos de los costes, equivalencia flujo/corte, casos vacíos y determinismo. Las pruebas sobre HCC_002 comprueban integración y trazabilidad, no precisión clínica.

**Después, evaluar anatomía con referencia independiente.** Un experto debe definir el objetivo tumoral global y anotar sin tomar la candidata como verdad. Las discrepancias requieren adjudicación y registro del criterio. Si la misma persona corrige visualmente la candidata, esa corrección es útil para estudiar el trabajo asistido, pero no es automáticamente una referencia independiente para demostrar su precisión.

Comparar, frente a esa referencia, tanto la máscara original como la refinada. Presentar Dice y una medida de borde como HD95 en milímetros, además del error de volumen y los fallos relevantes por lesión. Un Dice alto contra la propia máscara usada como inicialización solo demuestra conservación o concordancia con ella; no demuestra mejora. [Definiciones y límites de métricas de segmentación](https://pmc.ncbi.nlm.nih.gov/articles/PMC4533825/).

Para medir ahorro de trabajo, comparar el tiempo necesario para llegar a una delimitación aceptable mediante corrección manual y mediante refinamiento seguido de revisión. Contabilizar el tiempo de inspección y los resultados rechazados, además del tiempo del algoritmo. Alternar o aleatorizar el orden de las tareas y registrar la experiencia de los revisores ayuda a reducir aprendizaje y sesgo de orden.

Separar por paciente el conjunto usado para ajustar radio y pesos del usado para evaluación final. Las dos adquisiciones del mismo paciente y sus cortes no constituyen observaciones independientes. Definir antes de evaluar cómo se tratarán máscaras vacías, lesiones múltiples y fallos; no omitirlos para mejorar el promedio.

## Alternativas consideradas

| Método | Qué ofrece | Motivo para no elegirlo como primera implementación |
| --- | --- | --- |
| Corte de grafo con franja y restricciones | Combina intensidad y vecinos, con regiones explícitamente fijas. | Es la opción elegida; requiere controlar memoria, cuantización y sensibilidad de sus costes. |
| Contorno activo geodésico | Desplaza un contorno hacia zonas con señal de borde. | Depende del mapa de bordes y del equilibrio entre suavizado y expansión/contracción; puede detenerse en estructuras internas. |
| Chan–Vese | Puede separar regiones sin un borde marcado, usando diferencias estadísticas de interior y exterior. | Su contraste entre regiones no representa necesariamente el objetivo tumoral heterogéneo; requiere decisiones adicionales para fijar núcleo y franja. |

La documentación oficial de MorphGAC explica la dependencia del preprocesamiento y el efecto de la fuerza de expansión o contracción. MorphACWE admite imágenes y volúmenes, pero requiere que interior y exterior difieran en promedio. Sus interfaces citadas no exponen un parámetro de espaciamiento físico; usarlas directamente exige resolver la anisotropía y las restricciones de la franja. La función `chan_vese` convencional de scikit-image se documenta para 2D, mientras que `morphological_chan_vese` admite 3D: no deben confundirse. [API oficial de segmentación](https://scikit-image.org/docs/stable/api/skimage.segmentation.html).

Estas alternativas no se descartan como inferiores en exactitud; no se han comparado en una evaluación de Axivol. La elección actual prioriza control explícito del cambio y reutilización de dependencias existentes.

## Fuentes y alcance de la revisión

- **Boykov y Jolly, 2001.** *Interactive graph cuts for optimal boundary & region segmentation of objects in N-D images*. Fundamento de términos de región, borde y restricciones. DOI y autores verificados mediante Crossref: [10.1109/ICCV.2001.937505](https://doi.org/10.1109/ICCV.2001.937505). Se consultó el texto completo del [PDF de los autores](https://www.csd.uwo.ca/~yboykov/Papers/iccv01.pdf).
- **SciPy.** Documentación oficial de [flujo máximo](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.csgraph.maximum_flow.html) y [distancia euclidiana con `sampling`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html). Fundamenta las restricciones de implementación; no aporta validación de este refinamiento médico.
- **Moawad y colaboradores, 2023.** Artículo de [HCC-TACE-Seg](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/), DOI [10.1038/s41597-023-01928-3](https://doi.org/10.1038/s41597-023-01928-3). Fundamenta la procedencia y el objetivo de las anotaciones publicadas.
- **Chan y Vese, 2001.** *Active contours without edges*. Referencia de la alternativa regional: DOI y autores verificados mediante Crossref, [10.1109/83.902291](https://doi.org/10.1109/83.902291). La comparación de funciones disponibles se basa en la documentación oficial de scikit-image; no se reprodujeron los experimentos del artículo.
- **Márquez-Neila, Baumela y Álvarez, 2014.** *A Morphological Approach to Curvature-Based Evolution of Curves and Surfaces*. DOI, autores y año verificados mediante Crossref: [10.1109/TPAMI.2013.106](https://doi.org/10.1109/TPAMI.2013.106). Referencia indicada por la API de los contornos morfológicos.

Revisión de fuentes: 11 de noviembre de 2026. El radio, los pesos y el criterio de aceptación de esta adaptación son decisiones del proyecto; no deben atribuirse a los artículos como recomendaciones clínicas.
