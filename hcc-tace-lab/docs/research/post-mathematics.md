# Matemáticas del seguimiento POST, con ejemplos pequeños

Esta guía explica la ruta automática y sus experimentos: **coordenadas → registro → localización → imagen POST → candidata → medida**. Todos los números de ejemplo son sintéticos; no describen pacientes ni resultados HCC_002/HCC_006.

El protocolo **2.0.0**, desplegado localmente, utiliza registro global, refinamiento hepático y contexto XY local. El registro y el cálculo del campo se comparten con el ejecutor experimental; otros tamaños de contexto, ventanas y modelos siguen siendo comparadores separados. [La guía del método](../automatic-post-segmentation.md) describe el flujo de la app. El crecimiento semiautomático por semilla tiene [otra explicación matemática](../post-segmentation-math.md); no se debe confundir con la red neuronal de esta guía. La verificación técnica no convierte las candidatas en referencias anatómicas.

## 1. Diccionario de símbolos

| Símbolo | Significado | Unidad |
| --- | --- | --- |
| `z, y, x` | Índices de corte, fila y columna, desde cero. | Índices, sin unidad física. |
| `Δz, Δy, Δx` | Distancia entre centros de vóxeles vecinos. | mm. |
| `O` | Centro del primer vóxel de la adquisición ordenada. | mm, coordenadas LPS. |
| `u, v, n` | Direcciones de los ejes de imagen; `n = u × v`. | Vectores unitarios. |
| `p` | Punto físico del paciente. | mm, LPS: izquierda, posterior, superior. |
| `T` | Transformación que localiza PRE en POST. | Entrada y salida en mm. |
| `I` | Intensidad de TC calibrada. | HU. |
| `ℓ` | Logit de máscara producido por la red. | Adimensional. |
| `M` | Máscara binaria predicha. | 0 o 1. |
| `N`, `V` | Cantidad de vóxeles positivos y su volumen. | Conteo y cm³. |

## 2. De `[z,y,x]` a un punto del paciente

`array[2,4,6]` identifica una celda de memoria. Por sí sola no dice dónde está en el cuerpo. La relación proviene del origen, orientación y espaciado DICOM.

En nuestra retícula regular:

$$
p = O + x\,\Delta x\,u + y\,\Delta y\,v + z\,\Delta z\,n,
\qquad n=u\times v.
$$

`u` es el primer triplete de `ImageOrientationPatient`: avanzar por una fila cambia la **columna x**. `v` es el segundo: avanzar por una columna cambia la **fila y**. `PixelSpacing` almacena **`[Δy, Δx]`**, no `[Δx, Δy]`. `ImagePositionPatient` señala el centro del primer vóxel. [DICOM, Image Plane Module](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html).

El código ordena los cortes por posición proyectada sobre `n` y calcula `Δz` a partir de esas posiciones. No utiliza sin comprobar el valor `SliceThickness` como separación. El cargador exige una retícula regular sin desplazamiento lateral entre planos; rechaza mezclas incompatibles en lugar de aplicar esta ecuación a cualquier serie.

### Ejemplo con ejes rotados

Sean `O=(10,20,30)` mm, `u=(0,1,0)`, `v=(-1,0,0)` y, por tanto, `n=(0,0,1)`. Usamos `Δz=2,5`, `Δy=0,8`, `Δx=0,5` mm. Para `[z,y,x]=[2,4,6]`:

$$
p=(10,20,30)+(0,3,0)+(-3{,}2,0,0)+(0,0,5)
=(6{,}8,23,35)\ \mathrm{mm}.
$$

El ejemplo muestra por qué multiplicar simplemente `[z,y,x]` por tres espaciados no basta: falta orientar los ejes y sumar el origen.

Si `D=[u\ v\ n]` es ortonormal, el regreso es:

$$
\begin{bmatrix}x\\y\\z\end{bmatrix}
=\operatorname{diag}(\Delta x,\Delta y,\Delta z)^{-1}D^T(p-O).
$$

El resultado puede ser fraccionario. No se redondea cada paso del registro. En código: [`index_to_patient` y `patient_to_index`](../../hcclab/registration.py).

## 3. PRE → POST significa transformar puntos, no copiar una máscara

La transformación rígida tiene la forma:

$$
T(p)=R(p-c)+c+t,
$$

donde `R` rota, `c` es el centro de rotación y `t` traslada. Con `fixed=PRE` y `moving=POST`, SimpleITK devuelve la dirección **PRE físico → POST físico**. Para un centro PRE:

$$
q_{POST}=\operatorname{patientToIndex}_{POST}
\left(T\left(\operatorname{indexToPatient}_{PRE}(q_{PRE})\right)\right).
$$

No se invierte `T` en esa operación. Para muestrear la imagen POST sobre la rejilla PRE se evalúa igualmente `I_POST(T(p_PRE))`; de ahí la convención que a veces parece contraintuitiva. [Convención oficial de SimpleITK](https://simpleitk.readthedocs.io/en/master/registrationOverview.html).

**Ejemplo:** al punto anterior le aplicamos una traslación sintética `(4,-2,5)` mm, sin rotación: obtenemos `(10,8;21;40)` mm. Si la rejilla POST tiene origen cero, orientación identidad y espaciado ZYX `(2;1;0,5)` mm, corresponde a `[20;21;21,6]`. Ese índice fraccionario es válido como localización continua; una operación posterior puede seleccionar un corte entero.

El algoritmo transforma el centroide y las ocho esquinas de la caja PRE. A partir de sus extremos POST obtiene una caja alineada con sus ejes y un margen de búsqueda. Internamente los límites son semiabiertos: `[lo, hi)`, por lo que el tamaño es `hi-lo`. Para la caja 2D que recibe SAM, el último centro de píxel es `hi-1`.

**El centroide no identifica necesariamente tejido viable.** La caja orienta la búsqueda; no es el contorno POST ni impone su volumen. El orden de las rotaciones Euler debe reconstruirse con los parámetros fijos de SimpleITK, incluido `ComputeZYX`; no deducirse del orden ZYX de los arrays.

## 4. Qué optimiza el registro

Para una transformación candidata se observan pares de intensidades:

$$
A=I_{PRE}(p),\qquad B=I_{POST}(T(p)).
$$

La información mutua resume cuánto informa una intensidad sobre la otra:

$$
MI(A,B)=\sum_{a,b}p_{AB}(a,b)
\log\frac{p_{AB}(a,b)}{p_A(a)p_B(b)}.
$$

Los términos con probabilidad conjunta cero aportan cero. Con logaritmo natural la unidad es **nat**. Mattes estima estas distribuciones a partir de intensidades muestreadas; no compara dos tablas perfectas conocidas. SimpleITK minimiza una métrica, por eso registra **MI negativa**: un valor más negativo puede representar mayor asociación estadística, no peor ajuste.

**Ejemplo binario:** dos intensidades equilibradas e independientes dan `MI=0`. Si siempre coinciden, `MI=ln(2)≈0,693` nats. Si siempre están invertidas, también pueden dar el mismo MI. Esta propiedad permite relacionar apariencias diferentes, pero demuestra por qué MI alta no prueba identidad tumoral.

### Global y refinamiento hepático

| Etapa | Qué usa para el registro | Qué no determina |
| --- | --- | --- |
| Global, en la aplicación | Retícula auxiliar de 3 mm, HU limitados a `[-200,400]`, máscara corporal y registro rígido. | No verifica fase equivalente ni que el objetivo sea la misma lesión. |
| Hepática, en el protocolo 2 | Inicialización global y métrica restringida en PRE a `Liver ∪ Mass`; contexto corporal POST. | No segmenta el hígado POST ni genera una lesión POST desde el SEG. |

La unión evita suponer que `Liver` incluye ya `Mass`. El refinamiento **actualiza los parámetros desde la solución global y devuelve la transformación final**. No se aplica después `T_final(T_global(p))`: eso duplicaría una transformación ya incorporada en la inicialización.

El registro puede mejorar numéricamente y desplazar el prompt a otro tejido. La comprobación independiente requiere puntos anatómicos o identidad experta, descritos en [el protocolo](post-tace-validation.md#7-medir-registro-identidad-y-contorno-por-separado). La app y el [runner experimental](../../experiments/post_tracking.py) llaman al mismo helper `refine_pre_to_post`, cuyas constantes están en [registro](../../hcclab/registration.py). Si el refinamiento falla, la app se detiene: no vuelve silenciosamente al registro global.

## 5. De HU a la entrada de la red

Primero se obtiene intensidad calibrada desde el valor almacenado `P`:

$$
I=mP+b.
$$

La [guía del baseline](../post-segmentation-math.md#1-de-los-píxeles-almacenados-a-las-unidades-hounsfield) detalla pendiente, intercepto y validez de las unidades. Luego el adaptador aprendido aplica nivel `L` y ancho `W`:

$$
u=\operatorname{clip}\left(\frac{I-(L-W/2)}{W},0,1\right).
$$

Con `L=40`, `W=400` HU:

| HU sintéticos | Valor normalizado `u` |
| ---: | ---: |
| −160 | 0 |
| 40 | 0,5 |
| 240 | 1 |
| 1000 | 1 |

Los valores 240 y 1000 se vuelven indistinguibles después de este recorte. Una ventana amplia modifica la información disponible al modelo; no garantiza que reconozca mejor la lesión. Los HU originales permanecen intactos.

Se repite `u` en tres canales y se aplica la normalización del modelo:

$$
u_k^*=\frac{u-\mu_k}{\sigma_k},\quad
\mu=(0{,}485,0{,}456,0{,}406),\quad
\sigma=(0{,}229,0{,}224,0{,}225).
$$

Esto no convierte la TC en información de color real. Es la preparación esperada por el predictor. El padding se representa con cero antes de esta normalización y se excluye de la máscara final.

La ventana de entrada de la red y el recorte HU auxiliar del registro son operaciones distintas. También son diferentes de cambiar los controles de visualización del visor: estos no reajustan por sí solos el modelo ya ejecutado. [Métodos de MedSAM2](https://arxiv.org/html/2504.03600v1), [implementación fijada](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py).

## 6. Logits, máscara y retorno a la rejilla POST

La red produce un valor continuo `ℓ` por posición. La sigmoide es:

$$
\sigma(\ell)=\frac{1}{1+e^{-\ell}},\qquad
M=\mathbf 1[\ell>0]=\mathbf 1[\sigma(\ell)>0{,}5].
$$

Para `ℓ=-2`, la sigmoide es aproximadamente `0,119`; para `ℓ=0`, es `0,5` y el umbral estricto lo deja fuera. Son puntuaciones del modelo: **no probabilidades clínicas calibradas de tumor**. Los experimentos también registran por corte un logit de presencia del objeto; es otro valor, distinto de estos logits de máscara.

En el protocolo 2 se recorta únicamente la entrada XY. Para una caja localizada de tamaño `n_y × n_x`, se solicita un lado físico:

$$
S=\max\left(160\ \mathrm{mm},\;2\max(n_y\Delta y,n_x\Delta x)\right).
$$

Por eso «contexto 160» significa un mínimo, no un campo fijo para cualquier lesión. Cada eje convierte ese lado a píxeles con su propio espaciado y redondea hacia arriba. El helper compartido [`context_bounds`](../../hcclab/inference_field.py) ajusta el campo a la imagen y comprueba que contenga la caja; no modifica z. Los 160 mm son una decisión de desarrollo, no un margen clínico validado.

Si los límites son `[y0:y1,x0:x1]`, el plano tiene tamaño `h=y1-y0`, `w=x1-x0`; todos los cortes z permanecen. Se redimensiona la imagen a 512 × 512 con interpolación bilineal. El predictor devuelve logits al tamaño `h×w`, también por interpolación cuando es necesario, **antes de aplicar el umbral**.

Con `align_corners=False`, la correspondencia entre centros en un eje de redimensionado es:

$$
a_{entrada}=(a_{salida}+0{,}5)\frac{n_{entrada}}{n_{salida}}-0{,}5.
$$

Es una estimación entre muestras, no nueva información adquirida. «Salida nativa» significa que la máscara final coincide con la rejilla original; no significa que la red nunca haya redimensionado internamente.

Para reinsertar la máscara:

$$
M_{nativa}[z,y_0+i,x_0+j]
=\mathbf 1[\ell_{local}[i,j]>0]\land Q[z,y_0+i,x_0+j],
$$

donde `Q` indica píxeles válidos. Fuera del campo XY se almacenan ceros, pero esa región **no fue evaluada**. No se pinta allí tejido inventado ni se interpreta automáticamente como ausencia. La app conserva los límites del campo y se abstiene si la predicción toca un borde XY artificial interior a la adquisición. No recorta una máscara final a la caja PRE para hacerla pasar. [Adaptador de la aplicación](../../hcclab/post_model.py), [comparador experimental](../../experiments/tracking_core.py), [retorno de logits del predictor fijado](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/efficient_track_anything/efficienttam_video_predictor_npz.py).

**Ejemplo de offsets:** en el recorte `[100:104,200:206]`, la celda local `[2,3]` corresponde a la celda nativa `[102,203]` del mismo corte. Su posición física se calcula con la geometría POST, no con la del recorte ni con PRE.

## 7. Contar y convertir unidades

Para una máscara nativa regular:

$$
N=\sum_{z,y,x}M[z,y,x],\qquad
V=\frac{N\,\Delta z\,\Delta y\,\Delta x}{1000}\ \mathrm{cm}^3.
$$

La división entre 1000 convierte mm³ a cm³. Con `N=12 000`, `Δz=2,5`, `Δy=0,8`, `Δx=0,5` mm, cada vóxel ocupa `1 mm³`; el volumen predicho es `12 cm³`.

Para un corte con 500 píxeles positivos:

$$
A=\frac{500\times0{,}8\times0{,}5}{100}=2\ \mathrm{cm}^2.
$$

Estos cálculos describen el tamaño de una máscara. No demuestran qué tejido contiene. En una candidata sin revisar se debe conservar esa condición; tampoco corresponde convertir el cambio de volumen en respuesta clínica.

## 8. Por qué consistencia no es exactitud

Dos máscaras `A` y `B` tienen:

$$
Dice(A,B)=\frac{2|A\cap B|}{|A|+|B|}.
$$

Si cada una contiene 100 vóxeles y comparten 80, su Dice es `0,8`. Si las dos son predicciones, el número mide concordancia entre ellas. Solo comparando con una referencia POST independiente y un objetivo definido se puede estudiar exactitud. Dos máscaras idénticas del órgano equivocado tendrían Dice 1 entre sí.

El control espacial utiliza:

$$
f_{fuera}=\frac{N-N_{dentro\ de\ ROI}}{N},\qquad N>0.
$$

Que `f_fuera=0` demuestra únicamente que la predicción está contenida en la región de búsqueda. El prompt y esa región comparten registro y pueden compartir error. La regla de rechazo actual no es una referencia anatómica. [Métricas 3D de Taha y Hanbury](https://pmc.ncbi.nlm.nih.gov/articles/PMC4533825/), [protocolo de evaluación y casos vacíos](post-tace-validation.md#8-abstenciones-vacíos-y-denominadores).

Para pasar de fórmulas a evidencia, el siguiente paso es [el piloto de anotación independiente](post-tace-validation.md#11-próximo-estudio-mínimo-realizable), no escoger la máscara cuyo volumen parezca más convincente.
