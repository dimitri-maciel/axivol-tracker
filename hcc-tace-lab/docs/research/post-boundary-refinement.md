# Refinamiento experimental del borde POST

**Estado: implementación 2.1.0 desplegada localmente y experimento reproducido en la aplicación.** Fecha: 13 de noviembre de 2026. Las dos ejecuciones descritas conservan fuentes y artefactos verificables. Este documento no acredita una mejora anatómica ni un cambio de respuesta al tratamiento.

## 1. Qué intenta resolver

La candidata automática localiza una región y propone su máscara. Este experimento estudia si es posible ajustar **solo su borde**, combinando la puntuación del modelo con la continuidad tridimensional y los cambios de intensidad de la TC POST. El contorno inicial, los logits y el resultado refinado se conservan por separado.

El objetivo sigue siendo la lesión tratada completa correspondiente a `Mass`, cuya definición y delimitación POST requieren confirmación experta. No se cambia el objetivo a «los vóxeles más brillantes», depósito de Lipiodol o tejido viable. La [revisión de evidencia](post-tace-evidence.md#2-qué-queremos-delimitar) explica esas diferencias. Una máscara más lisa puede omitir tejido; una energía menor no demuestra un borde más correcto.

La función implementada es [`post_boundary.refine_post_boundary(post_hu, initial_mask, logits, spacing_zyx, band_mm=2.0, *, valid_mask=None)`](../../hcclab/post_boundary.py). Devuelve una copia binaria y diagnósticos. No recibe una máscara PRE ni emplea un hígado predicho para recortar la lesión. Reutiliza el solver de corte mínimo del [refinamiento PRE](../automatic-refinement.md), pero **no** sus histogramas de intensidad ni su término de conservación de etiquetas.

## 2. Entradas y procedencia

Las tres matrices comparten la retícula POST nativa, en orden `[z, y, x]`:

| Entrada | Significado |
| --- | --- |
| `post_hu` | Intensidad POST en unidades Hounsfield, obtenida de los píxeles DICOM con sus factores de reescala. |
| `initial_mask` | Máscara booleana anterior al refinamiento. No se sobrescribe. |
| `logits` | Puntuaciones continuas de la misma inferencia que produjo esa máscara, ya devueltas al campo nativo. |
| `spacing_zyx` | Separación entre centros de vóxel `[dz, dy, dx]`, en mm. |
| `valid_mask` | Soporte utilizable: respeta el padding DICOM y el campo que el modelo realmente evaluó. No transforma píxeles no evaluados en fondo confirmado. |

En el soporte válido se exige `initial_mask == (logits > 0)`. Una discrepancia obliga a revisar la procedencia; no se corrige cambiando silenciosamente la máscara. Un foreground inicial sobre padding se rechaza. Los valores ausentes fuera del campo de inferencia no deben rellenarse con una confianza inventada ni participar en los términos del grafo. El runner debe declarar ese campo y detectar si el refinamiento queda limitado por un borde artificial.

### El corte inicial necesita sus logits originales

La llamada `add_new_points_or_box` devuelve una predicción a partir de la imagen y la caja. Después, el seguimiento utiliza `add_new_mask` para incorporar una máscara binaria como ancla. Las puntuaciones que el predictor devuelve para esa ancla pueden codificar directamente la máscara suministrada: **no son una nueva estimación de confianza obtenida de la imagen**.

Por ello, el experimento conserva en el corte inicial los logits originales de `add_new_points_or_box`, antes de introducir el ancla binaria. En los demás cortes conserva los logits de propagación. Esta elección debe quedar registrada; no basta con buscar un valor numérico concreto y asumir que toda puntuación de ese valor es artificial. El umbral cero reproduce la máscara de partida de esa misma pasada. No se mezclan logits de una ejecución con una candidata antigua obtenida con otro registro o prompt.

## 3. Dónde puede cambiar la máscara

Sea `M₀` la máscara inicial. Para cada centro de vóxel se calcula su distancia euclidiana al centro más próximo de la **clase opuesta original**, utilizando `distance_transform_edt` con `sampling=(dz, dy, dx)`:

\[
d_i=\begin{cases}
\operatorname{dist}(i, M_0=0), & M_{0,i}=1,\\
\operatorname{dist}(i, M_0=1), & M_{0,i}=0.
\end{cases}
\]

Solo son variables los vóxeles válidos con `dᵢ ≤ b`. El núcleo interior y el exterior restantes conservan exactamente su etiqueta. La distancia se calcula contra la máscara inicial, no se recalcula para permitir expansiones sucesivas. La banda predeterminada es **2 mm**; las comparaciones de sensibilidad prefijadas son **1 y 3 mm**. El contrato admite de 1 a 5 mm, pero eso no convierte todos esos valores en parámetros evaluados o clínicamente aceptables.

**Una banda de 2 mm no equivale a desplazar cualquier borde 2 mm.** En un tramo plano cuyo único vecino de la clase opuesta está en el siguiente corte, una separación `dz=2,5 mm` deja ambos centros fuera de esa banda. No se inventa un corte intermedio ni se mueve medio vóxel. En otra parte del mismo corte puede existir un vecino opuesto lateral a menos de 2 mm y sí permitirse un cambio. Con 3 mm se incorporan otras posibilidades entre cortes. Esta discontinuidad procede del muestreo nativo; debe describirse al comparar las variantes.

La regla limita el conjunto de etiquetas modificables. No significa que el error frente al límite anatómico real sea inferior a 2 mm.

## 4. Qué energía se minimiza

Cada variable `yᵢ` vale 1 para lesión y 0 para fondo. El logit `ℓᵢ` es positivo cuando la red favorece lesión y negativo cuando favorece fondo. Definimos `softplus(t)=log(1+exp(t))`, evaluada de forma numéricamente estable.

\[
E(y)=\sum_i D_i(y_i)+\sum_{\{i,j\}\in\mathcal N}w_{ij}[y_i\ne y_j],
\]

\[
D_i(1)=V\,\operatorname{softplus}(-\ell_i),\qquad
D_i(0)=V\,\operatorname{softplus}(\ell_i),\qquad V=dz\,dy\,dx.
\]

El primer término favorece la clasificación de la red. Por ejemplo, **ilustrativo, no de HCC_006**: con `ℓ=2`, `softplus(−2)≈0,127` y `softplus(2)≈2,127`; clasificar ese vóxel como lesión cuesta menos. Con `ℓ=−2` se invierte la preferencia. Con `ℓ=0` ambas etiquetas cuestan lo mismo y los vecinos pueden decidir. Aunque estas expresiones corresponden al logaritmo negativo de una sigmoide, aquí no se ha demostrado que esa sigmoide esté calibrada como probabilidad de tejido correcto.

El segundo término penaliza etiquetas diferentes entre **seis vecinos por cara**:

\[
w_{ij}=\lambda A_{ij}\exp\left[-\frac12\left(\frac{I_i-I_j}{\sigma}\right)^2\right],
\qquad A_{ij}=\frac{V}{h_{ij}},\quad
\lambda=0,5\ \mathrm{mm},\quad\sigma=20\ \mathrm{HU}.
\]

`Iᵢ` e `Iⱼ` son las intensidades POST; `hᵢⱼ` es `dz`, `dy` o `dx`, según el eje que une los centros. `Aᵢⱼ` es el área de la cara compartida, en mm². Así, la separación anisótropa participa explícitamente: no se trata un salto entre cortes como un salto lateral idéntico. `V` pondera el coste individual en mm³ y `λA` da la misma escala dimensional al término entre vecinos; esta convención fija cómo comparar sus pesos, no una energía física del tejido.

Cuando dos intensidades son parecidas, separar sus etiquetas cuesta más. Ante un contraste fuerte, esa penalización disminuye y el grafo puede situar un borde allí. **Un borde fuerte también puede estar dentro de la lesión**, por ejemplo alrededor de una región hiperdensa. Los logits y las anclas limitan, pero no eliminan, ese riesgo. Los seis vecinos pueden introducir preferencia por direcciones de la retícula; continuidad 3D no equivale a una superficie perfectamente isotrópica.

Los vecinos congelados aportan su penalización al coste de la etiqueta variable que discrepa con ellos. No hace falta asignar capacidades infinitas. No hay histograma foreground/background, umbral HU que defina el tumor, erosión/dilatación aplicada a la salida, relleno de huecos ni selección automática de la componente mayor.

## 5. Resolución, límites y diagnósticos

El problema binario con pesos no negativos se resuelve mediante corte mínimo/flujo máximo. Se reutiliza el motor SciPy existente: capacidad base de 256 por unidad de energía, reducción de escala si es necesaria y suma total acotada a `10⁹`, con comprobaciones antes de convertir a `int32`. El mínimo corresponde al grafo **cuantizado**, no exactamente a una energía continua ni al contorno anatómico.

El contrato compara también las energías en coma flotante. Si el resultado aumenta la energía o la reducción no supera la tolerancia numérica, devuelve la máscara original. Una ejecución puede terminar como `unchanged`; no se amplía la banda automáticamente para obligarla a producir cambios. Se mantienen límites de 64 millones de vóxeles de volumen, 4 millones en la región de trabajo y 300 000 nodos variables.

Los valores registrados como `energy_before` y `energy_after` omiten la misma constante común de los costes individuales, tras incorporar los vecinos fijos. **Se compara antes frente a después dentro de una misma banda.** Cambiar la banda modifica las variables, el grafo y la constante retirada: comparar sus energías absolutas para elegir la «mejor» banda sería incorrecto.

El runner conserva logits, máscara inicial, máscara refinada, manifiesto con hashes de fuentes y diagnósticos. Debe permitir identificar la serie/adquisición POST, el prompt y registro usados, el checkpoint, el código y cada parámetro. El resultado no reemplaza la candidata activa por el hecho de haberse ejecutado.

Los diagnósticos incluyen energías antes/después, escala del grafo, vóxeles añadidos y retirados, cortes modificados y la distancia máxima de cada grupo cambiado a la clase opuesta **original**. Esta última distancia verifica el alcance de la operación; **no es HD95 ni error respecto a una referencia anatómica**. El cambio de volumen se obtiene con `(añadidos−retirados)·V/1000` en cm³ y compara dos segmentaciones del mismo POST, no PRE frente a POST.

## 6. Cómo leer las pruebas

Las comprobaciones siguientes separan funcionamiento y sensibilidad de exactitud anatómica. Los resultados locales se detallan en la sección 7; no establecen una variante ganadora.

| Comprobación | Qué puede demostrar |
| --- | --- |
| Objetos sintéticos con logits, bordes y heterogeneidad controlados | Signos correctos de los costes, respuesta al contraste, anisotropía y funcionamiento numérico. |
| Comparación exacta fuera de banda; hashes antes/después | Conservación de anclas, datos fuente y máscara inicial. |
| Casos de padding, campo limitado, logits no finitos o incongruentes | Rechazo explícito de entradas incompatibles; no creación de evidencia ausente. |
| Bandas prefijadas 1, 2 y 3 mm con los mismos logits | Sensibilidad al alcance permitido, sin mezclarla con cambios de prompt o nueva inferencia. |
| Montajes axial, coronal y sagital con igual ventana | Dónde se añaden/retiran vóxeles y si aparecen discontinuidades; revisión de plausibilidad, no exactitud certificada. |

HCC_006 usa PRE adquisición 3, `Mass` 2 para la localización original, y POST adquisición 1. HCC_002 usa PRE y POST adquisición 1, `Mass` 2. **Ambos son casos de desarrollo** y ya influyeron en el método. No se elige por paciente la variante más agradable ni se presenta acuerdo con MedSAM2, nnInteractive o TotalSegmentator como validación.

Para afirmar mejor delimitación o menor esfuerzo de corrección hacen falta referencias POST independientes, definición experta del objetivo y medición del tiempo de revisión/corrección, incluyendo rechazos. El [protocolo de validación](post-tace-validation.md) y el [paquete de anotación](annotation-kit.md) separan esa evaluación del experimento de ingeniería.

## 7. Resultados locales de desarrollo

El runner [`experiments.post_boundary`](../../experiments/post_boundary.py) realizó **una inferencia por caso** y aplicó por separado las bandas de 1, 2 y 3 mm a esa misma máscara y esos mismos logits. No encadenó refinamientos. Ambos casos usan POST adquisición 1, separación nativa `[2,5; 0,78125; 0,78125]` mm y `Mass` PRE, segmento 2, para localizar el prompt; `Liver` PRE, segmento 1, participa únicamente en el contexto de registro.

| Caso | PRE | Retícula POST `[z,y,x]` | Ejecución local | Tiempo total del runner |
| --- | --- | --- | --- | --- |
| HCC_006 | Adquisición 3 | `[103,512,512]` | `run-20261113T211925-17cd15d6` | 59,163 s |
| HCC_002 | Adquisición 1 | `[97,512,512]` | `run-20261113T212052-93e1217e` | 51,456 s |

Los directorios están en `output/post-boundary/` del espacio de trabajo, fuera del repositorio. Cada uno contiene `comparison.json` y `manifest.json`. El tiempo total incluye las etapas del runner; no es solo tiempo de corte mínimo ni una garantía de rendimiento. Los UID completos, adquisiciones, SOP ordenados y geometrías están en sus manifiestos.

### Cambio de la máscara en la misma pasada

«Añadidos» y «retirados» se calculan respecto a la máscara cruda del **mismo caso y ejecución**. El porcentaje cambiado es `100 × (añadidos + retirados) / N_cruda`; no es error de segmentación.

| Caso | Banda | Vóxeles | Volumen de la máscara, cm³ | Añadidos / retirados | Vóxeles cambiados, % |
| --- | --- | ---: | ---: | ---: | ---: |
| HCC_006 | Cruda | 11 427 | 17,4362 | 0 / 0 | 0 |
| HCC_006 | 1 mm | 11 327 | 17,2836 | 146 / 246 | 3,4305 |
| HCC_006 | **2 mm** | **11 355** | **17,3264** | **167 / 239** | **3,5530** |
| HCC_006 | 3 mm | 11 348 | 17,3157 | 163 / 242 | 3,5442 |
| HCC_002 | Cruda | 413 728 | 631,2988 | 0 / 0 | 0 |
| HCC_002 | 1 mm | 413 462 | 630,8929 | 3 193 / 3 459 | 1,6078 |
| HCC_002 | **2 mm** | **413 118** | **630,3680** | **3 720 / 4 330** | **1,9457** |
| HCC_002 | 3 mm | 413 071 | 630,2963 | 3 750 / 4 407 | 1,9716 |

En HCC_006, 2 mm cambia 406 etiquetas y reduce el recuento neto en 72 vóxeles: `−0,10986328125 cm³`. En HCC_002 cambia 8 050 etiquetas y reduce el recuento neto en 610 vóxeles: `−0,9307861328125 cm³`. **Son diferencias entre dos máscaras del mismo POST**, no reducción del tumor por tratamiento. El parámetro principal de 2 mm se fijó antes de estas comparaciones; no se eligió por producir un volumen preferido.

La candidata histórica HCC_006 `379a27b9134e4e6b9f6b58d40ccce09e`, de 11 172 vóxeles, procede de otra pasada y versión del flujo. No es la máscara cruda de este experimento. Registro, localización y prompt se recalculan: sin un experimento controlado no se atribuye la diferencia entre 11 172 y 11 427 a una causa única, ni se presenta 11 172 → 11 355 como efecto del refinamiento.

### Alcance espacial y funcionamiento numérico

Las distancias son máximos desde los centros modificados a la clase opuesta **de la máscara cruda**; no son distancias a una delineación experta. La energía se compara únicamente dentro de cada fila.

| Caso | Banda | Vóxeles variables | Distancia máxima añadidos / retirados, mm | Energía antes → después |
| --- | --- | ---: | ---: | ---: |
| HCC_006 | 1 mm | 3 406 | 0,7813 / 0,7813 | 739,034 → 512,154 |
| HCC_006 | 2 mm | 8 117 | 1,5625 / 1,1049 | 792,753 → 565,227 |
| HCC_006 | 3 mm | 16 831 | 1,5625 / 1,1049 | 886,070 → 658,554 |
| HCC_002 | 1 mm | 30 308 | 0,7813 / 0,7813 | 10 636,423 → 7 193,538 |
| HCC_002 | 2 mm | 71 567 | 1,7469 / 1,7469 | 12 229,742 → 8 493,841 |
| HCC_002 | 3 mm | 160 587 | 2,5000 / 2,5000 | 15 676,684 → 11 926,571 |

Las seis variantes terminaron como `refined`, con reducción de energía dentro de su propia banda. Los tamaños finales de 1–3 mm fueron próximos en estas dos pasadas, pero esa sensibilidad limitada no prueba que la máscara cruda o refinada delimite el objetivo correcto. Se conservaron los intervalos z ocupados: índices 49–64 para HCC_006 y 46–86 para HCC_002, ambos inclusivos y basados en cero. No se forzó esa extensión mediante recorte.

En las máscaras crudas y refinadas se registraron cero vóxeles en padding, fuera del campo de inferencia o fuera de la ROI de búsqueda. Las bandas editables no tocaron los límites del campo y las máscaras no tocaron sus bordes XY artificiales. Esos controles verifican restricciones espaciales, **no identidad ni exactitud anatómica**. Tampoco certifican que no haya lesión fuera del campo evaluado.

Los manifiestos declaran `source_integrity_verified=true` y los SHA-256 de todos los artefactos coinciden con sus archivos. Conservan hashes CT/SEG antes y después, implementación, pesos y configuración del modelo. La ejecución HCC_006 precede a la incorporación del resumen `application_checks`; sus métricas y diagnósticos permiten comprobar las restricciones anteriores. HCC_002 incluye ese resumen y marca las tres variantes como `spatial_checks_passed`. El hash del runner difiere por esa adición de reporte; se preservaron ambos manifiestos históricos sin reescribirlos. No se calculó Dice ni HD95 frente a una referencia POST experta.

## 8. Reproducción y archivos

Con las series originales y el checkpoint fijado ya disponibles según el [README de experimentos](../../experiments/README.md), ejecutar desde `hcc-tace-lab` después de construir la imagen actual:

```bash
mkdir -p ../output/post-boundary
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/../output/post-boundary:/experiment-output" \
  --entrypoint python app -m experiments.post_boundary \
  --data /data --output /experiment-output \
  --manifest /app/experiments/manifest.example.json --case HCC_006
```

Para el segundo caso sustituir `HCC_006` por `HCC_002`. Ejecutar los casos secuencialmente. Cada invocación crea un directorio nuevo y conserva `raw.npz`, `logits-field.npz`, `refined-1mm.npz`, `refined-2mm.npz`, `refined-3mm.npz`, `comparison.json` y `manifest.json`. El manifiesto fija la selección de series, adquisiciones y segmentos; no usa una candidata histórica como entrada.

El servicio monta `/data` en solo lectura. El runner comprueba fuentes y hashes antes y después, no guarda candidatas en `/annotations` y no cambia la máscara activa de la aplicación. Los volúmenes y logits permanecen locales y no se incorporan a Git ni a la imagen Docker. Para auditar exactamente una pasada se conservan su código identificado por hashes y sus artefactos: una nueva ejecución con registro recalculado no se presume idéntica bit a bit. La comparación de bandas sí reutiliza la misma inferencia dentro de cada ejecución.

## 9. Integración y comprobaciones de software

La versión `2.1.0`, protocolo `liver_context_medsam2_boundary`, se reconstruyó y ejecutó en Docker. Pasaron **189/189 pruebas en 141,368 s**, incluido el piloto público HCC_002. Tras aclarar las claves de metadatos `initial_threshold_logit` y `output_rule`, las **19 pruebas del adaptador** pasaron de nuevo en 0,198 s. También se revisaron de forma independiente signos, áreas físicas, anclas, padding, límites del campo y trazabilidad del runner.

Desde el botón **Generate automatically** se guardó la candidata HCC_006 `25b9f264bf5b40dfa2dfc54cb4b30f6b`: PRE adquisición 3, `Mass` 2 y `Liver` 1 para localización; POST adquisición 1. Operación backend: **58,9301 s**. Raw: 11 427 vóxeles; salida: 11 355; añadidos: 167; retirados: 239. La máscara NPZ, los hashes raw/logits y los hashes CT ordenados PRE/POST coinciden exactamente con el experimento `17cd15d6`. Esto permite usar sus figuras como comparación de esta ejecución de la app.

La revisión de navegador pasó siete grupos de comprobaciones en HCC_002/HCC_006 y anchos de 1600, 1280 y 390 píxeles: ambos visores, teclado, cambio de caso, restauración, ocultar/mostrar máscara y pausa de herramientas. No produjo errores JavaScript ni escrituras inesperadas. La máscara anterior `379a27b9134e4e6b9f6b58d40ccce09e` permanece guardada. La nueva se mostró resaltada y con estado **Automatic mask · Unreviewed**, sin errores JavaScript durante la generación. El ajuste ya forma parte de las siguientes generaciones automáticas; no exige trazar la máscara a mano. Estas comprobaciones verifican funcionamiento y procedencia, no precisión anatómica.

## 10. Fuentes y alternativas

- **Boykov y Jolly, 2001.** [Texto completo de los autores](https://www.csd.uwo.ca/~yboykov/Papers/iccv01.pdf); [DOI 10.1109/ICCV.2001.937505](https://doi.org/10.1109/ICCV.2001.937505). Base del corte binario con términos individuales, bordes y anclas. Nuestra banda, parámetros y logits son decisiones adicionales; no una receta POST-TACE validada por ese artículo.
- **Christ et al., 2016.** [Método completo, CRF 3D en §2.4](https://arxiv.org/html/1610.02177v1#S2.SS4). Precedente de combinar probabilidades de CNN, posición e intensidad en TC hepática. Usa CRF denso e inferencia aproximada; nuestro grafo local binario no es una reproducción de ese método. Su estudio venoso 3DIRCADb no acredita rendimiento después de TACE.
- **Wu et al., 2017.** [Texto completo](https://pmc.ncbi.nlm.nih.gov/articles/PMC5635475/); [DOI 10.1155/2017/5207685](https://doi.org/10.1155/2017/5207685). Documenta dificultades con contraste débil y tumores heterogéneos junto al borde hepático. No se adopta su regla de buscar una semilla oscura para el POST tratado.
- **Malpani et al., 2022.** [Texto completo](https://pmc.ncbi.nlm.nih.gov/articles/PMC8972393/); [DOI 10.1016/j.jvir.2021.12.017](https://doi.org/10.1016/j.jvir.2021.12.017). Su objetivo es depósito de Lipiodol en CBCT intraprocedural, distinto de toda la lesión en TC POST. No se trasladan sus umbrales ni métricas a Axivol.
- **Implementaciones oficiales:** [SciPy `maximum_flow`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.csgraph.maximum_flow.html), [distancia euclidiana con `sampling`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html) y [SimpleITK GeodesicActiveContourLevelSet](https://simpleitk.org/doxygen/latest/html/classitk_1_1simple_1_1GeodesicActiveContourLevelSetImageFilter.html). El contorno geodésico es una alternativa disponible, pero depende del mapa de bordes y fuerzas de curvatura/propagación. No se añade a este experimento ni se considera que suavizar equivalga a mejorar.

La lectura crítica se apoyó en las skills de investigación y `scientific-critical-thinking`; el [crédito de las herramientas procedimentales](README.md#herramientas-de-apoyo-y-crédito) es independiente del fundamento y la validación del algoritmo.
