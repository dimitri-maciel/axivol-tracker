# Condicionamiento, seguimiento y preparación de la entrada POST

**Estado: tres intervenciones comparadas en HCC_006 y HCC_002; ninguna adoptada como default.** Caja directa y seguimiento sin salida multimáscara aumentaron la discontinuidad investigada en HCC_006. La preparación compatible con el loader oficial redujo modestamente los saltos, pero amplió un fragmento desconectado; en HCC_002 añadió un plano terminal ocupado. Se conserva el método anterior, sin presentar ninguna de estas salidas como referencia anatómica. Este documento complementa la [revisión por cortes de HCC_006](hcc006-slice-review.md) y el [método de refinamiento del borde](post-boundary-refinement.md).

## 1. El corte 55 no es el ancla

En la pasada HCC_006 `run-20261113T211925-17cd15d6`, el manifiesto registra `prompt_slice_index=59`: **corte 60 de la app**, porque el array empieza en cero. Los cortes 55 y 56 de la app son `z=54` y `z=55`; ambos se generan durante el mismo recorrido inverso desde `z=59`. No están en la unión entre las dos direcciones ni reciben una nueva caja.

La implementación procesa primero hacia índices crecientes y luego reinicia el estado para procesar hacia índices decrecientes. «Bidireccional» describe esos dos recorridos desde el ancla hacia extremos opuestos; **no implica dos predicciones independientes de cada corte ni una comprobación de acuerdo entre direcciones**.

La configuración fijada conserva el ancla y hasta seis memorias recientes (`num_maskmem=7`). Los dos planos señalados están a cinco y cuatro pasos del ancla, respectivamente: todavía no hay seis predicciones previas que obliguen a descartar la más antigua. Por tanto, no hay fundamento para atribuir esta discontinuidad a un cambio de ancla, a la unión de recorridos o a una expulsión concreta de memoria en ese punto.

## 2. Qué hace realmente el modelo

SAM2 combina características de la imagen actual con información almacenada de prompts y predicciones anteriores. Una predicción puede influir en las siguientes a través de memoria espacial y de un vector que representa el objeto, denominado *object pointer*. MedSAM2 reutiliza ese mecanismo para secuencias de cortes. Esto aporta contexto, pero no impone una superficie anatómica continua ni demuestra que el objeto seguido sea el objetivo deseado. [SAM2, arquitectura](https://arxiv.org/html/2408.00714v2#S4), [MedSAM2, método y limitaciones](https://arxiv.org/html/2504.03600v1).

El código y las líneas citadas corresponden al commit fijado `332f30d420f1d1b08e2a79b3ae6a602458808383`, no a una rama que pueda cambiar. El checkpoint usado sigue siendo `eff_medsam2_tiny_FLARE25_RECIST_baseline.pt`; no se atribuyen automáticamente a este checkpoint los resultados publicados para otros modelos MedSAM2.

### Variante anterior: `binary_reinjection`

1. `add_new_points_or_box` recibe la caja localizada y produce logits a partir de la imagen POST.
2. Se aplica el umbral cero para formar una máscara binaria.
3. `add_new_mask` introduce esa máscara como ancla. Sustituye los prompts de puntos/caja de ese corte y, con la configuración utilizada, acepta la máscara como salida directa.
4. Se propaga en ambas direcciones con un estado independiente por dirección.

La rutina upstream de máscara directa construye logits artificiales ±10, fija la puntuación de presencia a partir de la existencia de foreground y calcula el puntero del objeto desde esa entrada. No es una nueva medición de certeza sobre el tejido. Axivol ya conserva los logits originales de la caja para el refinamiento del corte inicial, pero esa conservación no cambia el ancla que se codifica para propagar. [Sustitución del prompt](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/efficienttam_video_predictor_npz.py#L312-L380), [salida directa y puntero](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/modeling/efficienttam_base.py#L417-L466).

Esta reinyección también aparece en el [runner RECIST upstream](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py#L396-L449). No se presenta como un error de implementación confirmado. La diferencia relevante de nuestro uso es que el ancla automática no ha sido corregida o confirmada por un experto.

### Variante comparadora: `box`

Se conserva el resultado condicionado por la caja y se propaga **sin llamar a `add_new_mask`**. Al iniciar la dirección inversa se reinicia el predictor y se suministra la misma caja en el mismo corte. La salida del ancla conserva los logits originales de la primera predicción con caja. No se agrega una caja al corte 55 ni se corrige a mano su extensión.

**Esto no significa memoria blanda o probabilística.** El builder establece `binarize_mask_from_pts_for_mem_enc=true`. Durante la preparación del recorrido, el predictor trata los cortes interactuados como `is_mask_from_pts=True`, y el codificador de memoria binariza su máscara. El experimento conserva ese comportamiento. Lo que puede cambiar incluye la representación del objeto, las puntuaciones originales del ancla y las conversiones de la máscara entre la retícula nativa y la entrada 512 × 512; no puede atribuirse el resultado exclusivamente a «conservar confianza blanda». [Builder](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/build_efficienttam.py#L175-L211), [preparación de memoria](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/efficienttam_video_predictor_npz.py#L492-L532), [binarización efectiva](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/modeling/efficienttam_base.py#L701-L715).

Para un futuro brazo de memoria blanda habría que cambiar y registrar ese atributo por separado. El builder CPU sobrescribe los overrides adicionales antes de agregar los suyos; desactivar todo `apply_postprocessing` cambiaría también otras opciones y no aislaría esta hipótesis. El presente experimento no lo hace.

### Otra hipótesis: selección entre alternativas

La configuración activa multimáscara durante el seguimiento y admite cero puntos. En un corte no interactuado se generan tres alternativas y se selecciona la de mayor IoU **estimada por la red**, junto con su puntero del objeto. Cambiar de alternativa podría producir una diferencia brusca de forma, pero la instrumentación descrita más adelante **no encontró un cambio de índice ganador entre los cortes 54–57 de la app**. No es IoU medida contra una anotación experta. La rama de fallback por estabilidad es distinta y se aplica cuando no se solicita salida multimáscara. [Configuración](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/configs/efficienttam_ti_512x512.yaml#L91-L119), [selección](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/modeling/efficienttam_base.py#L382-L390).

## 3. Por qué no rellenar automáticamente el corte 55

Para estudiar la discontinuidad se definió, sobre la máscara **cruda** de la pasada anterior:

\[
G=M_{53}\cap M_{55}\cap\neg M_{54}.
\]

Son píxeles marcados en los planos anterior y siguiente, pero no en el central: **96 píxeles**, equivalentes a 58,59375 mm² en ese plano. No constituyen un «hueco anatómico» demostrado ni necesariamente un hueco encerrado en 2D. Es una discrepancia entre predicciones vecinas, definida para análisis de desarrollo.

En esos mismos puntos del corte central, la mediana es **−48,5 HU** y la mediana del logit es **−2,36765**. La mediana del promedio de las dos diferencias absolutas con sus vecinos es **153,25 HU**:

\[
\operatorname{mediana}_{p\in G}
\frac{|HU_{54,p}-HU_{53,p}|+|HU_{54,p}-HU_{55,p}|}{2}.
\]

Se promedian diferencias absolutas, no intensidades vecinas que podrían cancelarse entre sí. La continuidad de las etiquetas y la imagen actual, por tanto, aportan señales diferentes. Estas cifras no identifican grasa, necrosis, aire, tejido viable ni otro componente: no se está interpretando clínicamente el estudio.

Además, **79 de los 96** están a más de 2 mm del foreground crudo según la distancia entre centros de vóxel usada por el algoritmo. La banda los mantiene fijos. Con separación entre cortes de 2,5 mm, un vecino foreground en el siguiente plano no basta para hacer editable ese centro con una banda de 2 mm. Interpolar etiquetas, cerrar huecos o aumentar la banda para llenar estos 96 píxeles impondría una respuesta antes de resolver su identidad.

Hay otra comprobación numérica independiente. Para cada uno de los 96 píxeles se calculó la máxima ventaja que podrían aportar todos sus vecinos Potts al cambiarlo de fondo a foreground, dividida por el volumen del vóxel:

\[
B(p)=\sum_{q\in\mathcal N_6(p)\text{ válido}}
\frac{0,5\ \mathrm{mm}}{h_{pq}}
\exp\left[-\frac12\left(\frac{HU_p-HU_q}{20\ \mathrm{HU}}\right)^2\right].
\]

Como todos esos logits son negativos, cambiar la etiqueta aumenta el coste individual en `−logit[p]·V`. **En 85 de los 96 se cumple `−logit[p] > B(p)`**: aun con todos los vecinos favoreciendo foreground, la preferencia individual por fondo supera la máxima compensación de esos términos de borde. La mediana de `B` es 1,08557. Este cálculo se hizo sobre los 96 antes de restringir a banda; los 85 y los 79 son conjuntos definidos por reglas distintas y **no se suman**. No es un umbral clínico ni una prueba de etiqueta correcta; explica por qué esta energía se resiste a rellenar esos puntos. Los valores se verificaron con las matrices originales, separación nativa y seis vecinos válidos.

Estas cifras pertenecen a la máscara cruda: al construir el mismo conjunto con la máscara refinada se obtienen **97 píxeles**, no 96. No se mezclan ambos conjuntos. La discrepancia es una señal para investigar el prompt y el seguimiento, no una orden de rellenado.

## 4. Comparación controlada

El runner [`experiments.post_prompt_conditioning`](../../experiments/post_prompt_conditioning.py) recibe una pasada de borde completada y verificada. No vuelve a registrar PRE y POST. Reutiliza exactamente su localización y exige que coincidan caja, corte del prompt, campo, checkpoint, configuración, normalización y ventana.

| Se mantiene fijo | Lo único que cambia |
| --- | --- |
| TC PRE/POST, series, adquisiciones, píxeles y geometría | `prompt_mode`: `binary_reinjection` → `box` |
| `Mass` y `Liver` originales que originaron la localización | Uso de la caja como condicionamiento sin reinyección de su máscara |
| Registro y localización guardados; campo XY; todos los cortes POST | |
| W400/L40, normalización, resize, checkpoint y configuración | |
| Codificación de memoria binaria en cortes interactuados | |
| Refinamiento posterior de 2 mm, costes y controles espaciales | |

No se cambia de fase de contraste ni se asume que las fases PRE/POST hayan sido verificadas clínicamente. No se elige una variante distinta para cada corte.

Primero el runner repite `binary_reinjection` y exige igualdad exacta de la máscara cruda y refinada con la pasada congelada. Si no reproduce ambas, detiene la comparación: una diferencia de repetición no debe atribuirse al prompt. Después ejecuta `box`, conservando máscaras crudas, refinadas y logits de ambas variantes. Verifica fuentes antes y después; un fallo produce un manifiesto `failed`, con causa y diagnósticos, sin seleccionar silenciosamente otro método.

## 5. Estado y lectura de resultados

Ambas ejecuciones terminaron como `completed`, con `replay_exact=true`, fuentes verificadas y SHA-256 de artefactos coincidentes. El replay reprodujo exactamente las máscaras cruda y refinada históricas; por eso esta comparación sí aísla el modo de condicionamiento dentro de la configuración fijada.

| Caso y adquisiciones | Directorio local | Cruda: binaria → caja | Refinada 2 mm: binaria → caja | Añadidos / retirados entre refinadas |
| --- | --- | ---: | ---: | ---: |
| HCC_006, PRE 3 / POST 1 | `run-20261113T235604-1ca375c1` | 11 427 → 11 269 | 11 355 → 11 099 | 290 / 546 |
| HCC_002, PRE 1 / POST 1 | `run-20261113T235847-29887e7c` | 413 728 → 411 706 | 413 118 → 411 237 | 3 958 / 5 839 |

Los directorios están en `output/post-prompt-conditioning/`, fuera de Git. Ambos casos usan `Mass` PRE, segmento 2, y `Liver`, segmento 1, con la misma función de localización que en su pasada congelada. Los volúmenes de las máscaras POST refinadas cambian de **17,3264 a 16,9357 cm³** en HCC_006 y de **630,3680 a 627,4979 cm³** en HCC_002. No son cambios del tumor entre momentos ni respuesta al tratamiento.

En HCC_006 la discontinuidad investigada **no mejoró**:

| Descriptor de máscaras refinadas | Reinyección binaria | Caja directa |
| --- | ---: | ---: |
| Píxeles del corte 55 de la app (`z=54`) | 462 | 448 |
| Píxeles del corte 56 (`z=55`) | 709 | 728 |
| Salto de centroide 55 → 56, solo en plano | 6,958 mm | 8,436 mm |
| Píxeles marcados en 54 y 56 pero no en 55 | 97 | 94 |

La pequeña disminución de ese último conjunto no compensa ni demuestra mejora de la transición: el salto del centroide aumentó y la extensión gris continúa siendo ambigua. No se selecciona caja directa solo porque produzca 256 vóxeles menos en el total. Es un resultado negativo para esta intervención concreta, no una demostración de que la variante anterior sea anatómicamente correcta.

Ambos brazos conservan la extensión z ocupada —49–64 en HCC_006 y 46–86 en HCC_002, índices de array inclusivos— y registran cero foreground en padding, fuera del campo o fuera de la ROI, sin contacto con bordes XY artificiales. Son controles de ejecución. Los tiempos totales del runner fueron 134,237 s y 96,722 s, respectivamente; cada uno incluye las dos inferencias y las comprobaciones. Las diferencias temporales entre brazos no son una comparación de rendimiento controlada.

Los resultados deben informar igualdad de replay, recuentos crudos y refinados, diferencias de etiquetas, volumen de cada máscara POST, extensión, componentes, cambios por corte y tiempo. Las métricas de acuerdo entre variantes describen sensibilidad, no exactitud. HCC_006 y HCC_002 son casos de desarrollo; HCC_002 sirve aquí como control de regresión, no como validación independiente. Las discrepancias en los cortes 55–56 se revisan junto con los demás cortes, no como único criterio de éxito.

### Instrumentación y segundo resultado negativo

El helper [`prompt_probe`](../../experiments/prompt_probe.py) registra puntuaciones, índice elegido, componentes y logits devueltos al campo nativo mediante la rutina upstream, sin cambiar la decisión. Exige que la alternativa seleccionada coincida exactamente con la salida real. Distingue el decoder usado solo para el puntero del ancla binaria. Las cinco pruebas sintéticas comprobaron captura sin cambiar la salida, tratamiento del padding, selección, restauración al fallar y restricciones de geometría; una revisión del helper contra el código upstream no encontró defectos materiales. La instrumentación se usa únicamente en el proceso experimental aislado.

En `run-20261114T000400-357e2f62`, HCC_006 reprodujo exactamente el baseline, con artefactos y fuentes verificados. El seguimiento original eligió siempre **índice 2**, contado desde cero:

| Corte app / array | Píxeles de las alternativas 0, 1, 2 | IoU estimada para la elegida | Índice elegido |
| --- | --- | ---: | ---: |
| 54 / 53 | 1 180; 434; 643 | 0,648 | 2 |
| 55 / 54 | 999; 307; 465 | 0,585 | 2 |
| 56 / 55 | 1 185; 516; 711 | 0,679 | 2 |
| 57 / 56 | 1 133; 669; 903 | 0,700 | 2 |

Las cuatro alternativas seleccionadas coinciden exactamente con los logits de salida de sus respectivos cortes. Esto descarta un **cambio de índice seleccionado** como explicación concreta del salto observado; no descarta cambios de forma dentro de la misma salida, efectos de la imagen ni de la memoria. El índice identifica una salida del decoder, no una identidad anatómica persistente.

La alternativa 0 del corte 55 incluye 39 de los 96 píxeles de `G`, pero añade 539 píxeles y retira 5 respecto a la alternativa seleccionada. Tiene cinco componentes 2D, frente a una en la seleccionada. Escogerla por rellenar parte de `G` introduciría una regla diseñada para ese corte, sin demostrar que sus extensiones correspondan al objetivo.

El segundo brazo conservó `binary_reinjection` y cambió únicamente `multimask_output_for_tracking` de `true` a `false`, restaurando el atributo al terminar. Mantiene caja, memoria binaria, checkpoint, ventana, campo, registro congelado y refinamiento de 2 mm. **No elimina toda selección de alternativas**: el fallback dinámico por estabilidad sigue habilitado en la rama de salida única. El atributo también puede cambiar qué representación se usa como puntero del objeto. Por eso el efecto causal corresponde a cambiar ese atributo en esta configuración; no puede interpretarse como una prueba aislada de «selección por IoU» ni de memoria blanda. [Decoder y fallback](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/modeling/sam/mask_decoder.py#L142-L158).

| Descriptor HCC_006 | Seguimiento original | `multimask_output_for_tracking=false` |
| --- | ---: | ---: |
| Vóxeles crudos | 11 427 | 11 626 |
| Vóxeles después del refinamiento de 2 mm | 11 355 | 11 501 |
| Añadidos / retirados respecto al original refinado | — | 459 / 313 |
| Píxeles refinados app 54; 55; 56 | 635; 462; 709 | 708; 476; 761 |
| Salto de centroide 54 → 55, en plano | 5,237 mm | 6,010 mm |
| Salto de centroide 55 → 56, en plano | 6,958 mm | 7,991 mm |
| Píxeles refinados presentes en 54 y 56, ausentes en 55 | 97 | 131 |
| Componentes 3D, conectividad 6 | 4 | 4 |

Ambos brazos conservan la extensión z=49–64, sin foreground en padding, fuera del campo o fuera de la ROI y sin contacto con bordes XY artificiales. El runner instrumentado tardó 144,872 s en total; no es una comparación de velocidad controlada. La salida única coincide con la salida real en los cuatro planos observados. Ambos saltos de centroide y el conjunto de discrepancia aumentaron: **tampoco cumple el criterio de no empeorar las transiciones investigadas**. No se requiere atribuir corrección anatómica al baseline para descartar esta sustitución como solución al problema planteado.

El control HCC_002 de seguimiento con salida única también terminó, en `run-20261114T001010-f26b9151`: la máscara cruda pasó de 413 728 a 453 052 vóxeles y la refinada de 413 118 a 451 753. Entre refinadas añadió 44 036 y retiró 5 401; las componentes 3D de conectividad 6 pasaron de tres a cuatro. Es una variación importante en el caso de control, no una medición de falsos positivos ni de crecimiento tumoral.

Antes de integrar cualquier variante se exigen fuentes y geometría intactas, ausencia de empeoramiento de ambos saltos, ausencia de nueva fragmentación o foreground en padding/campo no evaluado, revisión visual de toda la extensión y control HCC_002 sin nueva deriva importante. Pasar esas condiciones solo permitiría una candidata experimental `Unreviewed`; no demostraría exactitud. Con estos dos resultados negativos se mantiene el método existente, sin rellenar huecos ni suavizar formas a la fuerza.

La anotación POST independiente y la definición experta del objetivo siguen pendientes. Ninguna reducción de saltos, de componentes o de volumen permite por sí sola afirmar que se eliminó tejido incorrecto. El [protocolo de validación](post-tace-validation.md) describe la evidencia necesaria para esa afirmación.

## 6. Tercera hipótesis predeclarada: preparación de imagen

La revisión del código encontró una diferencia concreta, sin demostrar todavía que cause la discontinuidad. `_NativeFrames` transforma HU con W400/L40 en `float32` dentro de [0,1], redimensiona con interpolación bilineal de PyTorch, repite el canal tres veces y aplica la normalización ImageNet. El runner RECIST oficial recibe `imgs` **ya preparado en [0,255]**; cuando XY no es 512 × 512, lo convierte a `uint8`, RGB mediante Pillow y redimensiona sin indicar filtro. Para RGB, Pillow usa bicúbica por defecto. Después divide entre 255 y aplica los mismos valores de media y desviación por canal. El predictor NPZ recibe esos tensores directamente: no vuelve a normalizarlos mediante el loader de vídeos. [Conversión y resize oficiales](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py#L194-L215), [entrada y normalización](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py#L329-L362), [Pillow: filtro predeterminado](https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.resize).

El campo de HCC_006 es 205 × 205, por lo que esa diferencia sí afecta a sus entradas. No se detectó un error de orden de canales ni doble división entre 255: los tres canales son copias de gris antes de normalizarse; después difieren por las constantes ImageNet, igual que en el código oficial.

El brazo propuesto conserva la ventana y construye:

\[
u(H)=\operatorname{clip}\left(\frac{H+160}{400},0,1\right),
\qquad q(H)=\operatorname{uint8}(255u(H)).
\]

`uint8` trunca estos valores no negativos; no se redondea. El padding se lleva a cero antes de redimensionar. Se convierte `q` en RGB, se aplica `Image.Resampling.BICUBIC` explícito a 512 × 512, se divide entre 255 y se normaliza con medias (0,485; 0,456; 0,406) y desviaciones (0,229; 0,224; 0,225). La implementación conserva el orden numérico del helper: división y normalización en `float64` con constantes `float32`, seguidas de conversión a `float32` antes de la red. La corrida registra Pillow 13.0.0, fijado en dependencias. La TC nativa, sus HU, retícula, retorno de logits y cálculo del volumen permanecen intactos; solo cambia la representación que ve la red.

Esta es una comparación de un **conjunto definido de cuantización a 8 bits e interpolación bicúbica**, no una estimación separada del efecto de cada operación. Tampoco equivale a reproducir toda la preparación del entrenamiento: el script RECIST no define HU→[0,255], y la documentación del dataset FLARE accesible sin autenticación no permitió confirmar esa etapa. El demo de otro modelo MedSAM2 usa límites `DICOM_windows` del CSV DeepLesion por lesión, clipping y min–max del volumen resultante antes de convertirlo a `uint8`; no prescribe W400/L40 universal. Por ello mantenemos W400/L40 como elección de ingeniería existente, sin añadir min–max por corte ni buscar otra ventana que favorezca HCC_006. [Demo CT y conversión HU](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/medsam2_infer_3D_CT.py#L220-L224).

**Protocolo fijado antes de inferir:** el tercer brazo debe conservar `binary_reinjection`, seguimiento multimáscara original, caja y localización congeladas, todos los cortes z, campo físico XY, checkpoint, ventana, geometría y refinamiento de 2 mm. Primero debe reproducir exactamente el baseline. Después se compararán los mismos saltos app54→55 y 55→56, todos los cortes ocupados y sus vecinos, recuentos crudos/refinados, componentes, discrepancias, contacto con bordes y padding; HCC_002 seguirá como control de regresión. Los criterios de integración de la sección anterior no cambian al conocer el resultado. Un tensor distinto es evidencia de distinta preparación, no de mejor segmentación.

### Resultado HCC_006: cambio modesto, con una contrapartida

`run-20261114T001457-edcd3478` terminó con replay exacto, integridad de fuentes verificada y todos los SHA-256 de artefactos coincidentes. Conserva PRE adquisición 3, `Mass` segmento 2 y `Liver` segmento 1 como origen de la localización; la predicción utiliza exclusivamente la TC POST adquisición 1 para delimitar. El runner de los dos brazos duró 127,188 s.

| Descriptor de HCC_006 | `float_bilinear` | `upstream_uint8` |
| --- | ---: | ---: |
| Vóxeles crudos | 11 427 | 11 824 |
| Vóxeles refinados | 11 355 | 11 719 |
| Volumen de la máscara POST refinada | 17,3264 cm³ | 17,8818 cm³ |
| Añadidos / retirados respecto a la refinada anterior | — | 470 / 106 |
| Píxeles refinados app 54; 55; 56; 57 | 635; 462; 709; 901 | 634; 469; 708; 887 |
| Salto de centroide 54 → 55, en plano | 5,237 mm | 5,111 mm |
| Salto de centroide 55 → 56, en plano | 6,958 mm | 6,590 mm |
| Discrepancia refinada: presentes en 54 y 56, ausentes en 55 | 97 | 95 |
| Componentes 3D, conectividad 6 | 4 | 4 |
| Tamaños de las componentes, de mayor a menor | 11 342; 10; 2; 1 | 11 682; 34; 2; 1 |

Las dos transiciones investigadas disminuyen modestamente, sin desaparecer. La extensión ocupada permanece z=49–64 y no hay foreground en padding, fuera del campo o fuera de la ROI, ni contacto con bordes XY artificiales. Sin embargo, **el foreground separado de la componente principal aumenta de 13 a 37 vóxeles**. El islote de 34 se distribuye entre app59 (30 píxeles) y app60 (4); antes el islote correspondiente tenía 10 píxeles solo en app60. Su caja nativa semiabierta es `[[58,350,204],[60,355,214]]`. Mantener cuatro componentes no equivale a mantener la misma fragmentación.

Esta contrapartida impide concluir mejora global solo a partir de los dos saltos. Sus reducciones son 0,125 y 0,368 mm, ambas inferiores al espaciado XY de 0,78125 mm; un centroide admite variaciones subpíxel, pero esa reducción no demuestra corrección del límite. Los cambios son descriptores de las predicciones, no exactitud, falsos positivos ni evolución tumoral.

### Control HCC_002 y decisión

`run-20261114T001722-3b67373c` terminó con replay exacto, fuentes verificadas y hashes de artefactos coincidentes. La revisión de ambos casos también comprobó la TC POST actualmente disponible: mismos bytes DICOM ordenados, SOP, adquisición, geometría nativa y soporte válido. HCC_002 usa PRE 1, `Mass` 2 y `Liver` 1 para localizar, y POST adquisición 1 para predecir.

| Descriptor HCC_002 | `float_bilinear` | `upstream_uint8` |
| --- | ---: | ---: |
| Vóxeles crudos | 413 728 | 415 142 |
| Vóxeles refinados | 413 118 | 414 544 |
| Volumen de máscara POST refinada | 630,3680 cm³ | 632,5439 cm³ |
| Añadidos / retirados entre refinadas | — | 3 649 / 2 223 |
| Componentes 3D de conectividad 6 | 413 112; 4; 2 | 414 540; 4 |
| Extensión ocupada, array inclusivo | 46–86 | 46–87 |

El plano terminal adicional, `z=87` o app88, contiene 802 píxeles; antes estaba vacío. El anterior, app87, pasa de 1 087 a 1 204. Se conserva la ausencia de foreground en padding, fuera del campo o de la ROI y sin contacto con bordes XY artificiales. El runner completo duró 126,149 s. Ni la nueva extensión ni su volumen se consideran correctos o incorrectos sin referencia independiente.

**Decisión:** no promover el cambio de entrada. En HCC_006 no se cumplió de forma global el criterio previo de no agravar la fragmentación, y la pequeña reducción de los saltos no resuelve la extensión gris ambigua. La variación terminal de HCC_002 también exige revisión. Se conserva `float_bilinear` con reinyección binaria y seguimiento multimáscara; el experimento no sustituye la candidata activa. No se eliminan los islotes ni se rellena `G` para hacer pasar el criterio.

El avance verificable de esta ronda es un adaptador con modos explícitos, instrumentación del decoder sin alterar sus decisiones y **tres comparaciones controladas, cada una en dos casos de desarrollo**, con replay e integridad de fuentes. Los resultados negativos y mixtos se conservan para evitar repetir hipótesis o elegir parámetros solo por apariencia. No se han calculado métricas de exactitud contra una referencia POST experta, que sigue pendiente.

## 7. Figuras y verificación de esta entrega

En `output/post-prompt-conditioning/` se conservan seis paquetes finales:

| Variante | HCC_006 | HCC_002 |
| --- | --- | --- |
| Caja directa | `figures-HCC006-box-ttf-final` | `figures-HCC002-box-ttf-final` |
| Seguimiento de salida única | `figures-HCC006-single-ttf-final` | `figures-HCC002-single-ttf-final` |
| Entrada uint8 y bicúbica | `figures-HCC006-upstream-ttf-final` | `figures-HCC002-upstream-ttf-final` |

Cada paquete incluye `focus-W400.pdf` y `focus-W800.pdf` para app54–57, `orthogonal.pdf`, un `atlas.pdf` de toda la extensión ocupada y sus vecinos, PNG equivalentes, CSV/JSON de todos los cortes adquiridos y manifiesto de fuentes y archivos. «Before/after» compara versiones sobre la misma TC POST. Cian identifica el contorno anterior; ámbar, la variante; rosa y azul distinguen píxeles añadidos y retirados. Las matrices y figuras quedan locales; el repositorio contiene código, documentación y agregados.

La inspección cubrió los 21 PNG nuevos de entrada y los 44 de los dos primeros experimentos. Los 44 PNG anteriores conservaron exactamente sus bytes al corregir la exportación PDF. Se verificaron 24 PDF con fuentes TrueType incrustadas y sin Type3, y 101 hashes de artefactos. Registro local: `figure-font-qa-ttf-final.json`. El cierre de `PdfPages` también queda dentro del contexto de fuentes: fijarlas solo al construir la figura no era suficiente.

Tras construir y reiniciar la app se ejecutó:

```bash
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

Resultado: **253 pruebas en 138,493 s; 252 aprobadas y una omitida** porque Matplotlib es una dependencia opcional fuera de la imagen de la app. Esa prueba de exportación PDF y las otras nueve del plotter pasaron en el entorno de figuras: **10/10**. La suite incluye generación real del piloto HCC_002, controles de fuentes, geometría, padding, restauración del predictor y equivalencia del preprocesado. Log local: `tests-backend.log`. No son pruebas de exactitud anatómica.

La API `/health` respondió correctamente. La candidata HCC_006 `25b9f264bf5b40dfa2dfc54cb4b30f6b` permanece `unreviewed`; su hash `511fcc15336070f0aae794e418e97edcea5af7e5fca0188b60e2217c813bf940` coincide con el registrado antes de esta ronda. No se sustituyó por ninguna de las salidas experimentales.

## 8. Reproducir sin cambiar la candidata activa

Desde `hcc-tace-lab`, con imagen `app` construida con el adaptador actual y el checkpoint local:

```bash
mkdir -p ../output/post-prompt-conditioning
docker compose run --rm --no-deps -T \
  --volume "$PWD/experiments:/app/experiments:ro" \
  --volume "$PWD/../output/post-boundary:/frozen-boundary:ro" \
  --volume "$PWD/../output/post-prompt-conditioning:/experiment-output" \
  --entrypoint python app -m experiments.post_prompt_conditioning \
  --data /data \
  --baseline /frozen-boundary/run-20261113T211925-17cd15d6 \
  --output /experiment-output --after box
```

Para HCC_002, la pasada congelada es `run-20261113T212052-93e1217e`. Para la segunda ablación usar `--after single_tracking --probe`; para preparación de entrada, `--after upstream_input`. Ejecutar secuencialmente. Cada corrida crea un directorio nuevo y conserva `before.npz`, `before-raw.npz`, `before-logits.npz`, sus equivalentes `after-*`, comparación y manifiesto. Con `--probe` también conserva `before/after-decoder.json` y `before/after-alternatives.npz`: todas las llamadas quedan descritas y las matrices de alternativas solo se guardan para z=53–56. Los volúmenes permanecen locales, fuera de Git y del contexto Docker. Este runner no escribe en el almacén de candidatas ni cambia la selección de la aplicación.

Fuentes primarias consultadas el 13 de noviembre de 2026: MedSAM2, [DOI 10.48550/arXiv.2504.03600](https://doi.org/10.48550/arXiv.2504.03600), versión publicada en arXiv v1; SAM2, [DOI 10.48550/arXiv.2408.00714](https://doi.org/10.48550/arXiv.2408.00714), texto v2; código upstream fijado enlazado en las secciones anteriores. Se aplicó `model-assessment` a la interpretación de incertidumbre y alcance, con el [crédito procedimental existente](README.md#herramientas-de-apoyo-y-crédito).
