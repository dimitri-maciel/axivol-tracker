# Localización y segmentación automática POST

## Objetivo y alcance

**Estado del código, 12 de noviembre de 2026:** implementación `2.0.0`, protocolo `liver_context_local_medsam2`, desplegada localmente. La suite integrada con `HCC_PILOT_TEST=1` pasó 134/134 pruebas en 135.691 s; la revisión UI HCC_002 pasó ocho grupos de comprobaciones en un almacén QA aislado. La revisión UI HCC_006 también pasó seis comprobaciones. Estos resultados de software y los pilotos no demuestran exactitud anatómica. El [índice de investigación](research/README.md), las [matemáticas del seguimiento](research/post-mathematics.md) y el [registro de arquitectura](../experiments/ARCHITECTURE.md) separan evidencia, decisiones y trabajo pendiente.

El objetivo es proponer una máscara sobre la TC posterior a TACE utilizando la información PRE para localizar una región de búsqueda. El flujo separa **encontrar la región correspondiente** de **delimitarla con los píxeles POST**. La salida, cuando existe, es una candidata `unreviewed`; el registro tiene estado `estimated_unverified` hasta disponer de una comprobación anatómica independiente.

La información PRE utilizada por este flujo son los segmentos publicados `Mass` y `Liver`, no una edición manual ni un refinamiento PRE generado por Axivol. `Mass` define el objetivo de localización; la unión `Liver ∪ Mass` limita las muestras de un refinamiento del registro. Se exige exactamente una etiqueta `Liver` publicada y no vacía sobre la adquisición PRE seleccionada. Si falta o es ambigua, se informa el problema sin elegir otro segmento silenciosamente. Siempre se registra caso, estudio, serie, adquisición, segmentos y hashes de origen.

No se pretende determinar automáticamente respuesta al tratamiento, tejido viable ni necrosis. Un volumen de candidata POST describe el objeto que produjo el algoritmo en esa retícula; no es todavía una medición clínica de respuesta.

La ruta automática de esta revisión utiliza **registro rígido global → refinamiento con contexto hepático PRE → localización de `Mass` → Efficient MedSAM2 tiny con contexto XY local POST**. El modelo tiene pesos publicados; sus pesos no se entrenaron ni ajustaron en Axivol. Las decisiones de contexto y localización sí se exploraron con HCC_002/HCC_006: son casos de desarrollo, no evaluación independiente. Los métodos de crecimiento por HU y corte de grafo se investigaron como comparadores, pero su fuga hacia tejidos vecinos en el piloto impidió adoptarlos como salida automática principal. Si el modelo falla, no se sustituye por una máscara PRE ni por una salida HU silenciosa.

## Por qué hace falta este desarrollo

HCC-TACE-Seg proporciona imágenes antes y después del procedimiento, pero los SEG publicados corresponden a las anotaciones PRE. Las variables clínicas de seguimiento y las medidas de respuesta del artículo no equivalen a una máscara tridimensional POST. Por tanto, no podemos comparar un algoritmo POST contra una referencia 3D que el dataset no proporciona. [Artículo y descripción de los datos](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/).

El artículo describe el uso del conjunto para estudiar predicción de resultados a partir de información previa al procedimiento. Ese contexto es compatible con la decisión de anotar PRE, pero no establece una explicación explícita de por qué no se anotó POST. No debemos atribuir a los autores motivos de tiempo, coste o dificultad que no documentaron.

La etiqueta `Mass` del caso piloto representa un objetivo tumoral global. El artículo describe componentes viables y necróticos; no hay que reinterpretar esa etiqueta como «solo tejido vivo». Antes de construir una referencia POST habrá que definir cómo delimitar el objetivo tratado, qué componentes incluir y cómo resolver bordes inciertos.

## Qué aporta PRE y qué debe aportar POST

| Información | Uso en el flujo | Lo que no demuestra |
| --- | --- | --- |
| Geometría y HU PRE y POST | Estimar una correspondencia espacial. | Que el tumor mantenga su forma o tamaño. |
| `Mass` PRE publicada | Localizar centro y región de búsqueda. | Que esos mismos vóxeles sigan siendo tumor en POST. |
| `Liver ∪ Mass` PRE publicados | Restringir las muestras de MI durante el refinamiento del registro. | Que la unión sea una máscara hepática POST o una referencia del tumor tratado. |
| Imagen POST y pesos del modelo | Calcular una delimitación candidata en POST. | Que la región sea viable o que el tratamiento haya funcionado. |
| Referencia POST independiente | Evaluar la delimitación candidata. | No está disponible por el mero hecho de descargar el dataset. |

Copiar o deformar la máscara PRE y contar sus vóxeles no mide el cambio biológico de la lesión. Una transformación rígida conserva el volumen continuo del objeto: el posible cambio en conteo después de remuestrear una máscara reflejaría discretización, cobertura u otros efectos del procedimiento, no necesariamente respuesta tumoral. Por eso la máscara propagada no se admite como resultado de segmentación POST.

## Geometría física antes del registro

Los arrays del proyecto siguen el orden `[z, y, x]`. SimpleITK indexa puntos de imagen como `(x, y, z)` y necesita origen, espaciamiento y dirección física; crear una imagen desde el array no basta para conservar la geometría DICOM.

Con origen `o`, espaciamiento `(dx, dy, dz)` y ejes físicos unitarios `e_x`, `e_y`, `e_z`, el centro de un vóxel corresponde a:

$$
p(x,y,z)=o+x\,dx\,e_x+y\,dy\,e_y+z\,dz\,e_z.
$$

Se conserva el sistema físico DICOM y se trabaja en milímetros. Los ejes de la imagen son las columnas de la matriz de dirección de SimpleITK. El orden de sus valores serializados es por filas. Confundir ambas convenciones puede producir un registro visualmente plausible con orientación equivocada. [Conceptos físicos de SimpleITK 2.5.7](https://simpleitk.readthedocs.io/en/v2.5.7/fundamentalConcepts.html).

Cada adquisición se comprueba por separado. El registro no justifica mezclar cortes de adquisiciones distintas, ignorar separaciones irregulares ni sustituir referencias ausentes. Las transformaciones DICOM convierten los valores almacenados a HU; el padding se identifica como información no válida para inferir tejido.

## Registro rígido: qué se está estimando

En esta implementación se fija la convención:

- **Imagen fija: PRE.**
- **Imagen móvil: POST.**
- **Transformación estimada: puntos físicos PRE → puntos físicos POST.**

SimpleITK estima una transformación desde el dominio fijo hacia el móvil. Por tanto, `Execute(PRE, POST)` devuelve una transformación que puede aplicarse directamente a un punto PRE para localizarlo en POST. No se debe invertir una segunda vez. En una implementación que intercambiara fija y móvil habría que invertir la transformación para ese mismo uso. [Convención oficial de registro](https://simpleitk.readthedocs.io/en/v2.5.7/registrationOverview.html).

El modelo rígido tiene seis grados de libertad: tres rotaciones y tres traslaciones. Con centro de rotación `c`:

$$
T(p)=R(p-c)+t+c,\qquad R^\mathsf{T}R=I,\quad\det R=1.
$$

Puede corregir desplazamiento y orientación global. No modela deformación del hígado por respiración, cambios de volumen, desplazamiento local de tejidos ni desaparición de una región tras el tratamiento. Un buen ajuste de huesos o del abdomen completo puede coexistir con error cerca de la lesión.

El registro utiliza copias auxiliares de menor resolución para reducir trabajo. Una retícula auxiliar de 3 mm es una elección de procesamiento, no una modificación de los DICOM. La segmentación y el conteo finales deben regresar siempre al espacio nativo POST; el espaciado de la copia de registro no se usa para medir la candidata.

### Información mutua

La información mutua mide dependencia estadística entre intensidades de las dos imágenes:

$$
\operatorname{MI}(A,B)=\sum_{a,b}p(a,b)
\log\frac{p(a,b)}{p_A(a)p_B(b)}.
$$

Durante el registro se comparan `I_PRE(p)` e `I_POST(T(p))`. El estimador de Mattes aproxima las distribuciones necesarias. La ventaja práctica es que no exige igualdad directa de intensidad entre imágenes. Eso no lo hace inmune a diferencias de fase, artefactos o cambios anatómicos.

SimpleITK minimiza sus métricas; para información mutua utiliza una versión con signo negativo. Un valor más negativo puede indicar mayor información mutua bajo esa configuración. **No es un error en milímetros ni una probabilidad de registro correcto.** Tampoco se deben comparar valores calculados con distintas regiones, histogramas o muestras como si fueran una escala universal de calidad. [Métricas, muestreo y pirámides](https://simpleitk.readthedocs.io/en/v2.5.7/registrationOverview.html).

### Inicialización y reproducibilidad

La inicialización necesita solapamiento físico suficiente. Los centros de los volúmenes pueden diferir porque las exploraciones cubren longitudes distintas; alinearlos no garantiza que el hígado quede alineado. El contexto hepático y el entorno de la lesión deben inspeccionarse al evaluar la solución.

La configuración registra transformación inicial, centro, optimizador, niveles de resolución, suavizado, muestreo, semilla, iteraciones y motivo de parada. Las escalas del optimizador se estiman mediante `PhysicalShift`; no se exporta un vector de escalas numéricas. El muestreo emplea semilla fija; el registro global usa dos hilos y el refinamiento uno. Esto facilita la repetición, pero no promete igualdad bit a bit entre plataformas. Las versiones de librerías y plataforma también importan. [Reproducibilidad documentada por SimpleITK](https://simpleitk.readthedocs.io/en/v2.5.7/registrationOverview.html#reproducibility).

Que el optimizador termine no implica validación anatómica. Puede terminar por un paso mínimo, falta de mejora, límite de iteraciones o imposibilidad de evaluar suficientes puntos; esos motivos deben conservarse en los diagnósticos.

### Refinamiento usando contexto PRE

`refine_pre_to_post` recibe la transformación rígida global como punto inicial y vuelve a estimar sus seis parámetros, manteniendo fixed=PRE y moving=POST. La transformación final ya es PRE → POST: no se compone otra vez con el resultado global.

Sea `C = Liver_PRE ∪ Mass_PRE` y `V_PRE` la validez de píxel. La máscara de muestreo es `C ∩ V_PRE`. La unión incluye `Mass` porque `Liver` puede excluir la región tumoral; así ocurre con los segmentos originales 1 y 2 de HCC_006. Se estima la MI solo con muestras PRE en ese contexto y soporte corporal válido POST. No se introduce ninguna máscara PRE en el predictor de segmentación.

| Parámetro del refinamiento | Valor fijado |
| --- | --- |
| Retícula auxiliar y HU de registro | 3 mm; recorte `[−200,400]` HU |
| Métrica y muestreo | Mattes MI, 50 bins, 30 %, semilla 20261112 |
| Pirámide y suavizado físico | Factores 2/1; 1/0 mm |
| Optimizador | Paso regular; paso inicial 1.0, mínimo 0.02, relajación 0.5 |
| Iteraciones y tolerancia de gradiente | Hasta 150 por nivel; `1e−4` |
| Ejecución | Un hilo de optimización; límite cooperativo de 30 s |
| Comprobaciones | ≥64 vóxeles de contexto auxiliar, ≥32 puntos válidos de métrica, parámetros finitos, rotaciones ≤20° y traslación ≤100 mm |

Los límites son decisiones de ingeniería, no criterios clínicos. El contexto, la transformación inicial y los HU originales no se modifican. Los diagnósticos conservan ambos registros, conteos del contexto y padding excluido. Un fallo de refinamiento impide seguir a segmentación; no activa silenciosamente el registro global como alternativa.

## Del registro a una región de búsqueda POST

El centro y la caja del segmento PRE se expresan en coordenadas físicas y se transforman mediante `T`. Al transformar una caja hay que considerar sus esquinas, no solamente trasladar un par de índices: una rotación cambia sus extremos sobre los ejes POST.

Después se localiza una región en la retícula POST y se comprueba cobertura. Una caja fuera de la imagen o parcialmente cortada no debe convertirse silenciosamente en una lesión más pequeña. El margen de búsqueda es un parámetro de ingeniería; debe distinguirse de una estimación del crecimiento real del tumor.

La caja responde a «dónde buscar». No es una máscara de salida. Si el registro sitúa la caja sobre tejido equivocado, un algoritmo de segmentación puede producir allí un objeto bien delimitado pero anatómicamente incorrecto.

## Delimitación con evidencia POST

### Modelo aprendido y caja inicial

Se utiliza el checkpoint oficial `eff_medsam2_tiny_FLARE25_RECIST_baseline.pt` de [MedSAM2](https://github.com/bowang-lab/MedSAM2/tree/332f30d420f1d1b08e2a79b3ae6a602458808383). El código original incluye una [ruta de inferencia CPU para lesiones CT](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py). Axivol utiliza el predictor directamente: no necesita una máscara POST de referencia para ejecutar la inferencia ni fabrica una anotación RECIST.

La caja del segmento PRE, transformada a POST, indica el objetivo en el corte correspondiente a su centro. Se usa la caja **sin margen de búsqueda**. Internamente sus extremos son `[mínimo, máximo)` en orden `[z,y,x]`; al predictor se pasan las esquinas de píxel `[xmin,ymin,xmax−1,ymax−1]`. Esta conversión evita confundir el orden de ejes y el extremo exclusivo.

El modelo segmenta primero el corte indicado. Su propia máscara POST inicial sirve como contexto para propagar hacia cortes superiores e inferiores. Se procesan **todos los cortes de la adquisición POST**, incluidos los que quedan fuera de la extensión z de PRE. No se recorta la salida a la caja PRE ni se toma el volumen PRE como objetivo numérico. Por eso el objeto propuesto puede tener una extensión distinta.

Esta propagación completa es una adaptación de Axivol. El script CT RECIST original limita los cortes analizados alrededor de una indicación RECIST hecha en POST. El [predictor upstream](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/efficienttam_video_predictor_npz.py) recorre la secuencia indicada y no se detiene automáticamente cuando una máscara queda vacía. Su [cabeza de presencia de objeto](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/modeling/efficienttam_base.py) puede apagar una predicción en un corte y volver a producirla después. Ni la memoria entre cortes ni ese score garantizan conservar la identidad anatómica de una lesión tratada.

Esta arquitectura emplea características aprendidas de la imagen y memoria entre cortes. De manera esquemática:

$$
\ell_z=F_\theta(X_z,\text{caja inicial},\text{memoria POST}),
\qquad M_z=[\ell_z>0].
$$

`θ` son los pesos descargados; `X` es la imagen preparada para la red; `ℓ` son sus puntuaciones antes de la función sigmoide. El umbral cero equivale a una sigmoide mayor de 0,5, pero **no debe presentarse como probabilidad clínica calibrada de tumor**. La ecuación resume la entrada y decisión; no reemplaza la arquitectura detallada del repositorio original.

### Preparación de imagen y retícula de salida

La implementación 2 selecciona un campo XY de entrada alrededor del centro localizado. Para una caja semiabierta de dimensiones `n_y × n_x` y espaciados nativos `dy, dx`, su lado físico solicitado es:

$$
S=\max\left(160\ \mathrm{mm},\;2\max(n_y\,dy,n_x\,dx)\right).
$$

Se usan `ceil(S/dy)` filas y `ceil(S/dx)` columnas, limitadas por el campo de visión adquirido. El campo se centra en la localización, se desplaza hacia dentro de la imagen si hace falta y debe contener toda la caja. La caja entregada al predictor resta los offsets `(x_0,y_0)` del campo; conserva extremos de centros de píxel inclusivos. **El eje z no se recorta:** se procesan todos los cortes POST.

Esto cambia la imagen que ve la red, no recorta una máscara ya inferida. La regla se aplica igual a los casos compatibles; no utiliza un volumen PRE esperado. La elección de 160 mm procede de los experimentos de desarrollo y requiere validación independiente.

El prototipo fija una ventana de nivel `40 HU` y ancho `400 HU`. Es una decisión de ingeniería **sin validación específica en TACE**. Convierte el intervalo `[−160,240] HU` a `[0,1]`:

$$
u=\operatorname{clip}\left(\frac{HU+160}{400},0,1\right),
\qquad X_c=\frac{u-\mu_c}{\sigma_c}.
$$

Se repite la imagen gris en tres canales y se aplican las medias `(0,485;0,456;0,406)` y desviaciones `(0,229;0,224;0,225)` usadas por el predictor original. Si el campo de entrada no mide `512×512`, se redimensiona con interpolación bilineal (`align_corners=false`). El predictor devuelve puntuaciones a la altura y anchura nativas del campo; se aplica el umbral allí y se inserta el resultado en el volumen POST usando sus offsets. Los cortes z, su espaciamiento y su orden DICOM permanecen nativos. Una retícula final nativa no significa que la red no haya empleado redimensionamiento interno.

Los ceros almacenados fuera del campo XY significan **región no evaluada**, no fondo anatómico demostrado. `inference_field_yx`, sus dimensiones, la caja nativa y la caja local, el redimensionamiento y `outside_inference_field=not_assessed` quedan registrados. No se interpola una máscara PRE para llenar el exterior.

La ventana satura los valores altos: dos intensidades mayores de `240 HU` tendrán la misma entrada normalizada. Eso puede ocultar diferencias de Lipiodol y debe estudiarse. Los HU originales no se modifican. El padding se representa como entrada negra y se excluye de la máscara final, registrando cuántos píxeles predichos fueron descartados.

Se desactiva el relleno opcional de agujeros que upstream implementa mediante extensión CUDA; no se reemplaza por erosión, dilatación ni suavizado ocultos. El resto de la configuración del predictor queda versionado. El modelo se carga bajo `eval` y `torch.inference_mode`; no se calculan gradientes ni se actualizan pesos.

### Qué ocurrió con los métodos clásicos

Un crecimiento conectado de HU puede unir tumor, parénquima y vasos cuando sus intensidades se solapan. En HCC_002, adquisición POST 1, el comparador final alcanzó las seis caras de la región de búsqueda y ocupó aproximadamente el 62 % de ella; por eso se abstuvo. Esa observación es un fallo del comparador en este caso, no evidencia de superioridad estadística del modelo aprendido.

También se probó un corte de grafo con costes de región y continuidad local:

$$
E(L)=\sum_p D_p(L_p;I_{POST})
+\sum_{\{p,q\}\in\mathcal N}w_{pq}(I_{POST})[L_p\ne L_q].
$$

Su piloto tampoco aisló de manera útil la región buscada. El corte mínimo puede resolver exactamente una energía sin resolver el significado anatómico del objeto. [Fundamento original](https://www.csd.uwo.ca/~yboykov/Papers/iccv01.pdf). Estos comparadores quedan separados del predictor principal y no se utilizan como sustitución automática al fallar este.

El flujo debe admitir abstención. No se debe forzar una máscara no vacía porque exista `Mass` en PRE, devolver la máscara trasladada si falla el cálculo ni aumentar repetidamente el área o los umbrales hasta obtener un objeto. Si no se resuelve una región con evidencia suficiente, se registra el fallo y se conserva disponible la revisión asistida.

## Lipiodol, necrosis y contraste

Después de TACE, una región hiperdensa puede corresponder a depósito de material y no a realce de tejido viable. El realce es un cambio asociado a la administración de contraste y a la fase de adquisición; no puede establecerse solo porque un píxel tenga un HU alto en una imagen aislada.

En un estudio primario de TC espectral tras TACE, el depósito de Lipiodol produjo problemas de interpretación en TC convencional: una región de depósito se confundió con lesión residual y otra lesión quedó oculta. Es evidencia de una dificultad real de la imagen, no un resultado de Axivol ni una validación de nuestro algoritmo. [Xu y colaboradores](https://pmc.ncbi.nlm.nih.gov/articles/PMC4691528/), DOI [10.5812/iranjradiol.26009](https://doi.org/10.5812/iranjradiol.26009).

El objetivo tumoral global puede reunir tejido con intensidades diversas. Los vasos, el parénquima vecino, el depósito de material y los artefactos pueden compartir intensidades con alguna parte del objetivo. Por eso:

- HU alto no demuestra tumor viable.
- HU bajo no demuestra fondo ni autoriza eliminar una región necrótica.
- Localizar Lipiodol no demuestra haber delimitado todo el objetivo tratado.
- Una región de intensidad homogénea puede ser solo una parte de la lesión.

La definición del objetivo POST y la revisión experta de las fases disponibles preceden a cualquier evaluación de volumen viable. Este flujo no sustituye esa definición.

## Abstención y estados explícitos

Los controles de ingeniería detectan fuentes o geometría no compatibles, HU no interpretables, solapamiento insuficiente para registrar, transformaciones inválidas, localización fuera de cobertura, checkpoint ausente o alterado, salida vacía, forma incompatible y propagación incompleta. Un predictor que no recorra todos los cortes no se guarda como una segmentación terminada. El límite de ejecución es de 300 segundos, 256 cortes y 64 millones de vóxeles; son límites de recursos, no criterios clínicos de selección.

Si la candidata alcanza el borde de la adquisición, se registra una advertencia de cobertura posiblemente incompleta. El sistema no interpreta automáticamente ese borde como límite real de la lesión. Estos controles no prueban separación anatómica: una predicción incorrecta también puede ser no vacía, continua y quedar dentro de la imagen.

Si la máscara válida alcanza un borde **artificial** del campo XY de inferencia que queda dentro de la adquisición, se rechaza con `incomplete_input_field`. Se conservan las caras en `internal_field_touch_faces`. Un borde artificial podría truncar el objeto y no debe presentarse como delimitación completa; la revisión de un campo mayor sería otra ejecución, no una ampliación automática oculta. Este control se aplica después de excluir padding y antes del control de mayoría fuera de ROI.

Los umbrales empleados para estos controles son configuraciones del proyecto, no límites clínicos publicados. Un control favorable significa únicamente que no se activó ese motivo de rechazo. No demuestra que sea la misma lesión ni que su borde sea correcto.

Ante un fallo, el resultado debe distinguir el punto en que ocurrió: registro, localización o delimitación. Un fallo de registro no equivale a ausencia de lesión. Una segmentación vacía o no resuelta tampoco demuestra respuesta completa al tratamiento.

Las candidatas aceptadas por los controles conservan estado `unreviewed`; la transformación conserva `estimated_unverified`. Las mediciones clínicas POST y el porcentaje de cambio por tratamiento permanecen sin habilitar.

### Control de coherencia entre localización y propagación

Después de inferir todos los cortes y excluir el padding, se cuenta la máscara POST completa. Sea `N` su número de vóxeles y `N_dentro` el número que cae dentro de `roi_zyx`, la caja localizada con margen de búsqueda de 10 mm:

$$
f_{fuera}=\frac{N-N_{dentro}}{N}.
$$

Si `f_fuera > 0,5`, el sistema **se abstiene de guardar la candidata** con motivo `localization_propagation_mismatch`. Si la fracción es exactamente `0,5`, este control pasa. La región debe ser válida, estar dentro de la retícula POST y contener la caja inicial; de lo contrario, se rechaza antes de inferir. Una máscara vacía se rechaza por separado, antes de dividir entre `N`.

El control no borra vóxeles fuera de la caja ni devuelve la intersección máscara–región. La máscara completa es la que se examina; una candidata que supera este control conserva también sus vóxeles externos. El diagnóstico `localization_consistency` registra caja, conteos dentro y fuera, fracción, límite, estado `passed` o `failed`, y `mask_clipped_to_region=false`.

**El límite del 50 % es una regla de ingeniería elegida después de observar HCC_006.** No procede del artículo de MedSAM2 ni constituye un umbral clínico validado. Puede rechazar crecimiento real o un registro impreciso, y puede aceptar una estructura equivocada situada dentro de la región. HCC_002 y HCC_006 son casos de desarrollo de esta regla, no una evaluación independiente de sensibilidad o especificidad. Su finalidad actual es impedir que una discrepancia espacial grande se presente silenciosamente como candidata lista para revisión.

Al aplicar el control a las salidas examinadas:

| Caso y adquisición POST | Vóxeles dentro | Vóxeles fuera | Fracción fuera | Resultado del control |
| --- | ---: | ---: | ---: | --- |
| HCC_002, adquisición 1 | 444 793 | 0 | 0 % | Pasa; continúa `unreviewed`. |
| HCC_006, adquisición 1 | 16 847 | 167 959 | 90,88 % | Abstención; no guardar como nueva candidata. |

Los conteos corresponden a esas ejecuciones concretas y no sustituyen los resultados del primer piloto aislado descrito más abajo. Pasar el control no demuestra que el objeto sea tumor.

En la interfaz, el mensaje principal informa que no se creó una candidata automática y que una máscara previa, si existía, permanece intacta. Los detalles del intento indican que la predicción se extiende principalmente fuera de la región localizada y piden revisar alineación y selección del objetivo. No indica ausencia de tumor ni respuesta al tratamiento. Antes de repetir, comprobar las adquisiciones seleccionadas, la lesión PRE de origen, la localización POST y las fases de contraste; la revisión anatómica sigue siendo necesaria. El diagnóstico permite distinguir este rechazo de un fallo de descarga del modelo o de una imagen que no pudo leerse.

## Qué medir para evaluar el método

### 1. Verificación de geometría e implementación

Utilizar volúmenes sintéticos con transformaciones conocidas para comprobar dirección PRE→POST, conversión entre índices y milímetros, orientación oblicua, anisotropía, transformaciones inversas y localización de cajas. Comparar desplazamientos físicos conocidos, no únicamente la apariencia de una superposición.

Probar también estados sin solapamiento, imágenes uniformes, padding, geometría incompatible y localización fuera de campo. Verificar que una abstención no produzca una máscara alternativa silenciosa y que ningún cálculo modifique los DICOM o la anotación PRE publicada.

### 2. Error de registro con referencias independientes

Un revisor identifica puntos anatómicos correspondientes que no se hayan utilizado para optimizar el registro. Para cada par `p_i` PRE y `q_i` POST:

$$
\operatorname{TRE}_i=\|T(p_i)-q_i\|_2\quad\mathrm{mm}.
$$

Presentar distribución de errores, máximos y localización de los puntos. Los puntos próximos a la región hepática de interés son necesarios para interpretar la utilidad local; una buena correspondencia ósea lejana no basta. Registrar también incertidumbre y diferencias entre revisores al localizar esos puntos.

Si los puntos se usan para inicializar o ajustar el registro, reservar otros para evaluar. El error sobre los mismos puntos empleados en el ajuste no es una evaluación independiente del objetivo. Los autores de SimpleITK muestran por separado similitud y error sobre puntos correspondientes precisamente porque responden a preguntas distintas. [Artículo de los notebooks de SimpleITK](https://pmc.ncbi.nlm.nih.gov/articles/PMC5959828/).

### 3. Error de segmentación contra una referencia POST

Crear una referencia POST independiente según un protocolo de objetivo tumoral global, con revisión o adjudicación experta. No usar como verdad la máscara PRE trasladada, el resultado automático ni una anotación que simplemente acepte sus bordes sin revisión independiente.

Para candidata `A` y referencia `B`, calcular Dice (adimensional) y HD95 (milímetros). El volumen se calcula en la retícula original POST:

$$
V_{POST}=\frac{N_{POST}\,dx_{POST}\,dy_{POST}\,dz_{POST}}{1000}\;\mathrm{cm}^3.
$$

El error relativo de volumen contra una referencia no vacía es:

$$
\varepsilon_V=100\frac{V_A-V_B}{V_B}.
$$

Es error frente a la referencia POST, no porcentaje de respuesta PRE–POST. Deben predefinirse los casos de referencia vacía, máscaras vacías, múltiples lesiones y fallos. Los resultados omitidos o rechazados cuentan como parte de la evaluación. [Métricas y límites de segmentación](https://pmc.ncbi.nlm.nih.gov/articles/PMC4533825/).

### 4. Utilidad práctica

Medir el tiempo total de revisión y corrección, incluyendo candidatos rechazados y la inspección del registro. Comparar un flujo manual con el asistido usando orden de tareas controlado y revisores identificados. Un algoritmo rápido puede aumentar el trabajo si produce contornos difíciles de corregir.

Separar pacientes de desarrollo y evaluación; las adquisiciones o cortes de HCC_002 no son pacientes independientes. Si el método se ajusta mirando ese caso, sus resultados son demostración de ingeniería y generación de hipótesis. No constituyen validación externa ni permiten afirmar exactitud general.

## Reproducibilidad, herramientas y alternativas

Se incorpora **SimpleITK 2.5.7** para el registro rígido en CPU. La versión se verificó en [PyPI](https://pypi.org/project/SimpleITK/2.5.7/) y la licencia Apache-2.0 en el [archivo del tag v2.5.7](https://github.com/SimpleITK/SimpleITK/blob/v2.5.7/LICENSE). La inferencia utiliza Torch 2.5.1, Torchvision 0.20.1 e Hydra 1.3.2. La carga es diferida: abrir la aplicación o cambiar de corte no ejecuta ni carga el modelo.

Por ejecución se guardan fuentes PRE y POST con hashes, etiquetas PRE originales, parámetros Euler iniciales/finales de ambas etapas de registro, centro y convención `ComputeZYX`, dirección PRE→POST, retículas auxiliares y nativas, motivo de parada, controles de abstención, región POST localizada, parámetros de delimitación, versiones de software y hashes del código. Los parámetros permiten reconstruir la transformación; no se exporta una matriz independiente. Las candidatas se almacenan separadas de `/data` y se exportan con su procedencia.

`registration.context_segments` y `baseline_source.registration_context_segments` conservan los números y etiquetas realmente utilizados. `Mass` y `Liver` proceden del mismo SEG publicado, cuyo SOP y hash se registran una vez como fuente compartida. La lectura sigue el orden hash → verificación de cabecera fresca y etiquetas → decodificación → comprobación de hashes antes de guardar. No se acepta una etiqueta antigua de caché si el archivo cambió.

La candidata de esta revisión lleva `method.name=registered_post_segmentation`, `method.version=2.0.0` y `method.protocol=liver_context_local_medsam2`. Los hashes incluyen `registration.py`, `post_automatic.py`, `post_model.py`, `inference_field.py`, `model_assets.py` y `volume.py`. El modelo y su configuración conservan sus propias huellas. Un informe de abstención incluye fuentes y diagnósticos, pero no una máscara ni volumen sustitutos.

El límite de inferencia de 300 segundos es cooperativo: se comprueba entre cortes. No interrumpe una operación nativa bloqueada a mitad de un corte. El límite del registro se comprueba entre iteraciones del optimizador. Son controles de ejecución, no garantías de latencia estricta.

El artefacto concreto queda fijado así:

| Elemento | Identificador reproducible |
| --- | --- |
| Código MedSAM2 | Commit `332f30d420f1d1b08e2a79b3ae6a602458808383` |
| Configuración | `efficient_track_anything/configs/efficienttam_ti_512x512.yaml` |
| SHA-256 configuración | `55a0142a2d0076d3c434a8a35c23ccc16e7e42ef06153b571f147615befa070d` |
| Pesos | `eff_medsam2_tiny_FLARE25_RECIST_baseline.pt` |
| Revisión Hugging Face | `e4a6f35edd7e091619cbc0750f462f1574e23955` |
| Tamaño | 71 662 031 bytes |
| SHA-256 | `78cd67f41c1c2953026bd6e70a7648998079d08cb6ab0f75bbe289039695c103` |

El [código](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/LICENSE) tiene licencia Apache-2.0. La [ficha oficial de los pesos](https://huggingface.co/wanglab/MedSAM2/blob/e4a6f35edd7e091619cbc0750f462f1574e23955/README.md) declara CC-BY-SA-4.0 e indica uso de los pesos para investigación y educación. Son condiciones diferentes; no se debe atribuir la licencia del código a los pesos ni asumir autorización clínica o comercial por ejecutar el prototipo.

La denominación del checkpoint y el entrenamiento de referencia corresponden al baseline FLARE25 RECIST. No se ha establecido si HCC-TACE-Seg intervino en su preentrenamiento o desarrollo. Por tanto, este caso no permite reclamar evaluación externa independiente del modelo publicado. La transferencia a lesiones tratadas con TACE requiere evaluación propia.

Los pesos se descargan en `data/models`, separados del repositorio y de la imagen Docker. `model_assets.py` verifica tamaño y SHA-256; upstream los deserializa con `torch.load(..., weights_only=True)`. La aplicación monta `/data` en solo lectura. Las candidatas y sus metadatos se guardan en `/annotations`; los archivos CT y SEG originales no se reescriben.

### Mapa del código y comprobación local

| Archivo | Responsabilidad |
| --- | --- |
| `hcclab/registration.py` | Geometría, registro global, refinamiento de contexto y localización de la caja. |
| `hcclab/post_automatic.py` | Orquestación, diagnóstico y comparador HU explícito. |
| `hcclab/inference_field.py` | Campo XY físico, contención de caja y offsets sin modificar z. |
| `hcclab/post_model.py` | Preparación de POST, predictor CPU, propagación y controles. |
| `hcclab/model_assets.py` | Descarga y verificación del checkpoint. |
| `hcclab/lab.py` | Procedencia DICOM, permisos de operación y almacenamiento separado. |
| `tests/test_post_model.py` | Todos los cortes, cajas nativas/locales, anisotropía, padding y rechazo de borde artificial sin pesos. |
| `experiments/post_tracking.py` | Ablaciones desde TC/SEG originales, fuera del almacén de candidatas. |

Desde `hcc-tace-lab`, las comprobaciones pertinentes son `docker compose exec -T app python -m unittest discover -s tests -p test_post_model.py -v` y la suite completa indicada en el README. Los tests sintéticos verifican contratos de software; no demuestran exactitud sobre lesiones reales. El conjunto de integración público requiere datos descargados y checkpoint verificado.

La [guía del runner](../experiments/README.md) reproduce variantes y curvas con procedencia completa. Por ejemplo, con las fuentes originales ya ingeridas:

```bash
mkdir -p ../output/post-experiments
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/../output/post-experiments:/experiment-output" \
  --entrypoint python app -m experiments.post_tracking \
  --data /data --output /experiment-output \
  --manifest /app/experiments/manifest.example.json --case HCC_006 \
  --variants local160liver
```

La imagen Docker usada debe contener la revisión de código descrita. Este comando crea un ensayo separado, no una candidata de la app. El runner omite deliberadamente el rechazo final por mayoría fuera de ROI para conservar también fallos de ablación; por eso un resultado del runner no significa que haya superado todos los controles de producción. Consulte [ABLATION.md](../experiments/ABLATION.md) para resultados completos y límites.

### Comprobación integrada y UI del protocolo 2

La suite con `HCC_PILOT_TEST=1` aprobó 134/134 pruebas en 135.691 s. La prueba UI real HCC_002 se ejecutó en un contenedor aislado, con PRE adquisición 1, `Mass` 2 y `Liver` 1, y POST adquisición 1. Su única generación automática devolvió 308628 vóxeles, con máxima área en el corte 72 desde cero, campo XY `[151,0]–[473,322)` y ningún contacto con borde artificial. De esos vóxeles, 665 quedaron fuera de la ROI de búsqueda (0.2155 %), conservados en la candidata; no se recortó a la región PRE.

El endpoint tardó 49.028 s, de los cuales 46.651 s fueron inferencia. Mientras la respuesta seguía pendiente, cambiar cortes tardó 196 ms en PRE y 133 ms en POST durante la inferencia. El test verificó restauración de candidata con metadatos PRE demorados, conservación de borradores manuales, descarte de respuestas tardías al cambiar fuente y separación entre candidata y reporte clínico. Las comprobaciones de transporte posteriores reutilizaron la respuesta real; no fabricaron otra máscara ni ejecutaron nuevamente el modelo.

Se inspeccionaron capturas de escritorio y móvil y se comprobó navegación de ambos visores en anchos 1600, 1024, 390 y 320 píxeles, sin overflow ni errores JavaScript. Los ocho grupos de comprobaciones pasaron. Los artefactos permanecen en `output/post-improvement/ui002` del workspace local. Se retiró únicamente el contenedor QA, conservando la app de producción saludable. La revisión UI de HCC_006 también pasó seis comprobaciones. Estos resultados verifican comportamiento de aplicación y procedencia, no exactitud de sus contornos.

La comprobación UI HCC_006 usó PRE adquisición 3, `Mass` 2 y `Liver` 1, y POST adquisición 1. Pasaron generación real, identidades de fuente, superposición visible, ocultar/mostrar, restauración tras recarga y navegación de ambos visores. El tiempo total de UI fue 51.651 s, la operación backend 50.2888 s y la inferencia 48.474 s; son intervalos distintos. Los cambios de corte durante generación tardaron 58 ms PRE y 67 ms POST.

Una auditoría posterior de solo lectura verificó los bytes originales y la candidata guardada de 11172 vóxeles. Su caja nativa es `[49,310,173]–[65,359,221)` en `[z,y,x]`, sin padding, contacto de borde artificial ni vóxeles fuera de ROI. Conserva tres componentes 6-conexas de tamaños 11170, 1 y 1; no se eliminaron las componentes pequeñas. Los planos axiales, coronal y sagital se renderizaron desde los arrays nativos, con proporción física preservada para visualización.

Los hashes CT ordenados, SOP y retícula coincidieron exactamente con un ensayo anatómico separado de TotalSegmentator. Solo entonces se calculó solapamiento: 0 vóxeles con sus predicciones renales y 2152 (19.26 %) con su predicción de hígado. **Son comparaciones entre modelos, no exactitud ni identificación anatómica garantizada**; una máscara hepática automática puede excluir parte del objetivo tratado. La candidata no se recortó ni modificó. El montaje y el JSON permanecen locales en `output/post-improvement/ui006`.

### Resultados históricos anteriores al protocolo 2

El primer piloto aislado del adaptador sobre HCC_002, adquisición POST 1, procesó los 97 cortes en aproximadamente 50 segundos con dos hilos de CPU y un pico observado de 524 MiB. La caja inicial del corte 67, contando desde cero, fue `[52,255,210,398]` en coordenadas xyxy de centros inclusivos. La candidata contenía 443 727 vóxeles, sin padding y sin tocar las caras de la adquisición. Estos son **resultados de ejecución de un piloto**, no resultados de precisión ni mediciones clínicas. El tiempo y la memoria pueden variar en la aplicación y con otras adquisiciones. La revisión visual de cortes distribuidos comprueba fallos evidentes de presentación y extensión, pero no sustituye una referencia anatómica independiente.

Una prueba inicial con la caja ampliada 10 mm produjo 521 996 vóxeles. La diferencia evidencia sensibilidad al prompt; no demuestra que una candidata sea mejor que otra. Aquel prototipo adoptó la caja localizada sin margen por definición del flujo, no para aproximarse a un volumen esperado; el protocolo 2 conserva esa convención. La evaluación futura debe incluir perturbaciones de caja y error de registro, sin convertir el acuerdo entre predicciones en exactitud contra referencia.

### Fallo observado en HCC_006

Una ejecución posterior sobre HCC_006 utilizó PRE adquisición 3, segmento publicado 2 (`Mass`), y POST adquisición 1. La localización estimada señaló el corte POST 56, contando desde cero, con una caja z de `[49,62)`. La candidata generada se extendió de los cortes 4 a 59 y alcanzó su mayor área en el 32. La revisión visual sugirió que su continuación inferior había seguido una estructura de apariencia renal. La candidata se retiró de las propuestas activas y se conservó separada para auditoría; **no debe usarse como volumen de la lesión tratada**. No se publican aquí capturas de ese caso.

Las comprobaciones de fuentes y geometría no identificaron una inversión de ejes ni sustitución de adquisición. Eso no demuestra que el registro localizara correctamente la lesión. La explicación compatible con lo observado es una deriva de seguimiento, posiblemente favorecida por una localización o indicación inicial imperfecta; separar ambas causas requiere revisión anatómica.

La conectividad tampoco validó el resultado: una componente con conectividad de seis vecinos contenía 184 795 de los 184 806 vóxeles predichos y los 1 165 vóxeles del corte 56 de la candidata almacenada. La reproducción aislada del prompt produjo 1 166 píxeles; 1 140 se solapaban con esa componente dominante. La conectividad de 26 vecinos tampoco la separó. Por tanto, conservar la mayor componente conectada, o exigir conexión con el prompt, no habría eliminado el fallo. Una deriva puede ser continua y atravesar regiones contiguas.

Después de este fallo se incorporó el control de fracción fuera de la región descrito arriba: detecta esta discrepancia sin recortar la máscara ni cambiar sus bordes. El área por corte, la trayectoria física del centroide y la presencia o desaparición del objeto siguen siendo señales adicionales para estudiar; no se presentan como controles automáticos ya implementados. Ninguna de estas señales prueba identidad anatómica. Recortar la candidata a la extensión PRE, detenerla a una distancia elegida después de mirar el resultado o asumir que una máscara vacía implica respuesta completa produciría conclusiones injustificadas. La regla incorporada y cualquier ampliación requieren evaluación contra referencias POST independientes.

La candidata histórica fallida de HCC_006 se conserva, junto con metadatos y nota de revisión, en almacenamiento local de auditoría separado de las propuestas activas. No se la sobrescribe con un nuevo resultado. Esto se distingue de una abstención nueva: esta última no guarda una máscara de candidata, y su diagnóstico puede exportarse. Las candidatas previas permanecen intactas.

### Evidencia que motivó el protocolo 2

El [registro de ablaciones](../experiments/ABLATION.md) conserva todos los pilotos, incluidos resultados con deriva. En HCC_006, el contexto local de 160 mm eliminó la extensión inferior observada en el piloto, pero cambió sustancialmente la región predicha al variar ventana o localización. Un campo de 240 mm produjo una extensión de apariencia renal aun pasando el control de mayoría en ROI. El refinamiento hepático desplazó la predicción hacia una región de alta intensidad; no demuestra haber cubierto todo el objetivo tratado.

La reproducción con el CLI extraído, HCC_006 PRE adquisición 3 y POST adquisición 1, `Mass` 2 y `Liver` 1, recalculó ambos registros desde las fuentes y produjo 11120 vóxeles, cortes 49–64 y 0 % fuera de ROI en 50.83 s de inferencia. Es evidencia de ejecución de esa variante experimental, no validación clínica ni confirmación del despliegue del protocolo integrado. Las diferencias con los 11485 vóxeles del piloto que fijaba el registro previo quedan documentadas. HCC_002/HCC_006 siguen siendo casos de desarrollo.

Como apoyo al desarrollo y a la revisión se utilizaron skills de manejo DICOM, diseño de estudios, documentación, código e interfaz. La skill `pydicom` reconoce la biblioteca *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*, de Kassis y colaboradores, preprint de 2026, [arXiv:2609.00065](https://arxiv.org/abs/2609.00065), DOI [10.48550/arXiv.2609.00065](https://doi.org/10.48550/arXiv.2609.00065). Este crédito corresponde a instrucciones de apoyo; no identifica el método de segmentación ni aporta evidencia de su exactitud.

## Fuentes verificadas

- **HCC-TACE-Seg:** [artículo de Moawad y colaboradores](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/), DOI [10.1038/s41597-023-01928-3](https://doi.org/10.1038/s41597-023-01928-3). Procedencia, anotaciones y alcance del dataset.
- **SimpleITK 2.5.7:** [registro](https://simpleitk.readthedocs.io/en/v2.5.7/registrationOverview.html), [geometría y transformaciones](https://simpleitk.readthedocs.io/en/v2.5.7/fundamentalConcepts.html), [distribución](https://pypi.org/project/SimpleITK/2.5.7/) y [licencia versionada](https://github.com/SimpleITK/SimpleITK/blob/v2.5.7/LICENSE).
- **Xu y colaboradores, 2015:** estudio original sobre depósito de Lipiodol y lesión residual, [texto completo](https://pmc.ncbi.nlm.nih.gov/articles/PMC4691528/), DOI [10.5812/iranjradiol.26009](https://doi.org/10.5812/iranjradiol.26009). DOI y autores verificados mediante Crossref.
- **Yaniv y colaboradores:** *SimpleITK Image-Analysis Notebooks: a Collaborative Environment for Education and Reproducible Research*, [texto completo](https://pmc.ncbi.nlm.nih.gov/articles/PMC5959828/), DOI [10.1007/s10278-017-0037-8](https://doi.org/10.1007/s10278-017-0037-8). DOI y autores verificados mediante Crossref.
- **Boykov y Jolly, 2001:** [artículo original de cortes de grafo](https://www.csd.uwo.ca/~yboykov/Papers/iccv01.pdf), DOI [10.1109/ICCV.2001.937505](https://doi.org/10.1109/ICCV.2001.937505), verificado mediante Crossref. Fundamenta la energía; no valida esta adaptación POST.

Consulta de fuentes: 12 de noviembre de 2026. Las decisiones de implementación y los resultados del piloto deben distinguirse de los resultados publicados por esos autores.
