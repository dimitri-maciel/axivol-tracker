# Matemáticas del candidato POST, paso a paso

Este documento acompaña la [guía de segmentación POST](post-segmentation.md). Define exactamente qué calcula el algoritmo y qué significan sus números. **Todos los ejemplos numéricos son sintéticos y didácticos; ninguno es un resultado de HCC_002 ni de otro paciente.**

La idea central es simple: dentro de una caja, conservar los vóxeles que cumplen un intervalo de intensidad **y** tienen un camino hasta la semilla. Después, contar esos vóxeles y multiplicar por su tamaño físico.

## 1. De los píxeles almacenados a las unidades Hounsfield

Un archivo DICOM contiene valores de píxel y metadatos que indican cómo interpretarlos. Para la transformación lineal utilizada en este baseline:

$$
I(z,y,x)=m_z\,P(z,y,x)+b_z
$$

| Símbolo | Significado |
| --- | --- |
| `P` | Valor almacenado del píxel; no es necesariamente HU. |
| `m_z` | `RescaleSlope` del corte. |
| `b_z` | `RescaleIntercept` del corte. |
| `I` | Valor transformado a HU cuando los metadatos de esa TC lo establecen. |
| `z, y, x` | Índices de corte, fila y columna. |

Los parámetros de transformación deben comprobarse en los cortes correspondientes, no suponerse iguales por comodidad. También hay que distinguir píxeles de imagen de **padding**, el valor de relleno que no representa tejido. Ese relleno se excluye con una máscara de validez, sin asignarle una intensidad ficticia.

El [CT Image Module de DICOM](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.8.2.html) establece cuándo esas unidades son HU. El cargador acepta `RescaleType = HU` y la omisión permitida para CT `ORIGINAL`, no `LOCALIZER`, sin adquisición multienergía. No interpreta cualquier número reescalado como HU sin comprobar sus metadatos.

**Ejemplo sintético:** si `P = 1064`, `RescaleSlope = 1` y `RescaleIntercept = -1024`, el resultado es:

$$
I = 1\times1064-1024=40\ \mathrm{HU}.
$$

El intervalo `[30, 80]` admitiría ese vóxel. Aplicar ese mismo intervalo directamente al valor almacenado `1064` daría una decisión diferente y equivocada para este ejemplo.

La transformación de **ventana y nivel** viene después y sirve para mostrar la imagen. Comprime valores HU en una escala visible de grises; no es la entrada del algoritmo de segmentación. Una captura de pantalla no conserva necesariamente la información cuantitativa original.

## 2. Índices y región de interés

El volumen NumPy se organiza así:

```text
volume[z, y, x]
       │  │  └─ columna
       │  └──── fila
       └─────── corte, dentro de la adquisición ordenada
```

Los índices internos empiezan en cero. Si la forma del volumen es `(Z, Y, X)`, los últimos índices válidos son `(Z-1, Y-1, X-1)`.

La ROI es una caja:

$$
R=\{(z,y,x): z_0\le z<z_1,\ y_0\le y<y_1,\ x_0\le x<x_1\}.
$$

La primera esquina se incluye y la segunda se excluye. En Python:

```python
cropped = volume[z0:z1, y0:y1, x0:x1]
```

Por tanto, su número de vóxeles es:

$$
|R|=(z_1-z_0)(y_1-y_0)(x_1-x_0).
$$

### Conversión desde la interfaz

La interfaz muestra índices desde uno y permite elegir un primer y último elemento incluidos. Para convertirlos:

```text
inicio interno = primero mostrado - 1
final interno exclusivo = último mostrado
semilla interna = semilla mostrada - 1, en cada eje
```

**Ejemplo sintético:** elegimos cortes 11–15, filas 31–70 y columnas 21–80. La semilla está en el corte 13, fila 40 y columna 50.

```python
roi = [[10, 30, 20], [15, 70, 80]]
seed = [12, 39, 49]
```

La caja contiene `5 × 40 × 60 = 12 000` vóxeles. No contiene seis cortes: el 15 interno es el primer índice excluido. La semilla es global; dentro del recorte queda en:

```python
local_seed = [12 - 10, 39 - 30, 49 - 20]  # [2, 9, 29]
```

Ese cambio de coordenadas es necesario para leer su etiqueta en una matriz recortada.

## 3. El conjunto de vóxeles admisibles

Sean `L` y `U` los límites inferior y superior HU, y `Q(p)` la validez del píxel. Un vóxel puede participar si:

$$
A=\{p\in R: L\le I(p)\le U\ \land\ Q(p)=1\}.
$$

Las tres condiciones son simultáneas:

1. Está dentro de la caja.
2. Su intensidad pertenece al intervalo, incluidos ambos extremos.
3. Es un píxel válido, no padding.

Una semilla fuera de `A` es una entrada inválida. El programa no debe sustituirla por otro punto ni aceptar la etiqueta cero del fondo: hacerlo seleccionaría algo distinto de lo solicitado.

## 4. Conectividad: por qué el umbral no basta

El umbral puede aceptar varios grupos separados. Queremos únicamente el grupo conectado a la semilla.

Para un vóxel `p = (z, y, x)`, los seis vecinos posibles son:

$$
\mathcal N_6(p)=\{
(z-1,y,x),(z+1,y,x),
(z,y-1,x),(z,y+1,x),
(z,y,x-1),(z,y,x+1)
\}.
$$

Solo cuentan los vecinos que están dentro del volumen y del conjunto admisible. Son contactos por **cara**, no por diagonal.

La región final es:

$$
C=\{p\in A:\text{ existe un camino de vecinos de }\mathcal N_6
\text{ desde la semilla }s\text{ hasta }p,\text{ contenido en }A\}.
$$

La máscara binaria resulta:

$$
M(p)=\begin{cases}1,&p\in C,\\0,&p\notin C.\end{cases}
$$

### Un ejemplo pequeño

Imagina este único plano; no hay otros cortes que unan las zonas. Elegimos `[30, 80] HU`, toda la matriz como ROI y la semilla de 50 HU de la primera fila, segunda columna:

```text
Intensidades HU        Pasa el umbral         Componente de la semilla
 0  50  50   0          0  1  1  0              0  1  1  0
 0   0  50   0          0  0  1  0              0  0  1  0
50   0   0  50          1  0  0  1              0  0  0  0
```

El 50 de abajo a la derecha solo toca por diagonal el grupo de la semilla; queda fuera. El 50 de abajo a la izquierda también queda fuera. El resultado contiene tres vóxeles. En un solo plano, la conectividad por caras permite cuatro direcciones dentro de ese plano; en un volumen se añaden los cortes anterior y posterior.

### Relación con la implementación

El código calcula todas las componentes de `A` en el recorte y conserva la de la semilla:

```python
labels, _ = ndimage.label(
    admissible,
    structure=ndimage.generate_binary_structure(3, 1),
)
selected = labels == labels[local_seed]
```

Es la misma definición de conjunto que obtendríamos con una cola que recorriera vecinos desde la semilla. Etiquetar componentes es una estrategia de implementación; no cambia el criterio matemático. Las etiquetas numéricas son identificadores de grupos, no probabilidades ni categorías anatómicas.

La búsqueda se limita a la ROI. La implementación también recorre el volumen para comprobar valores finitos y devuelve una máscara de tamaño completo. Por ello, no debe describirse su coste total como si dependiera exclusivamente del tamaño de la caja.

### Propiedades comprobables

- La semilla debe pertenecer al resultado.
- La máscara es falsa fuera de la ROI y en padding.
- Todos sus vóxeles cumplen el intervalo HU.
- Todos están conectados con la semilla mediante seis vecinos.
- Con las mismas entradas y versiones, la matriz resultante es determinista.
- Ampliar el intervalo HU, manteniendo imagen, ROI, validez y semilla, no puede eliminar los vóxeles previamente conectados. Puede incorporar una región mucho mayor si aparece un puente entre estructuras.

La última propiedad explica por qué variar un umbral unos pocos HU puede cambiar mucho el volumen: basta con aceptar un pequeño camino que una dos grupos grandes. No implica que la lesión haya cambiado.

## 5. De índices a coordenadas físicas DICOM

`x` e `y` dentro de la matriz no son automáticamente los ejes físicos del paciente.

Definimos:

- `S_z`: `ImagePositionPatient` del corte `z`, en milímetros; corresponde al centro del primer vóxel del plano.
- `r`: primer vector de `ImageOrientationPatient`, dirección de una fila al avanzar entre columnas.
- `c`: segundo vector, dirección de una columna al avanzar entre filas.
- `dy = PixelSpacing[0]`: distancia entre filas.
- `dx = PixelSpacing[1]`: distancia entre columnas.

La posición del centro del vóxel es:

$$
P(z,y,x)=S_z+x\,dx\,r+y\,dy\,c.
$$

Esta notación evita una confusión frecuente: el primer valor de Pixel Spacing es `dy`, pero el primer vector de orientación multiplica el desplazamiento por columnas, `x × dx`. La definición procede del [Image Plane Module de DICOM](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html).

Los vectores `r` y `c` deben ser unitarios y ortogonales. La normal del plano es:

$$
n=r\times c.
$$

Proyectamos la posición de cada corte sobre la normal:

$$
t_z=S_z\cdot n.
$$

Ordenar por `t_z` sitúa los cortes en orden geométrico. Si la separación es regular:

$$
dz\approx t_{z+1}-t_z>0.
$$

Axivol comprueba esa regularidad antes de utilizar un único `dz`. Una adquisición de un solo corte no permite deducir por sí sola esa separación; tampoco una posición duplicada o irregular puede resolverse inventando un valor.

`SliceThickness` describe espesor nominal de reconstrucción. La distancia entre centros de planos describe muestreo espacial. Pueden ser diferentes; no son etiquetas intercambiables.

### Ejemplo sintético de posición

Con `S_z = (10,20,30) mm`, `r = (1,0,0)`, `c = (0,1,0)`, `dx = 0.8 mm`, `dy = 0.7 mm`, `x = 2` e `y = 3`:

$$
P=(10,20,30)+2(0.8)(1,0,0)+3(0.7)(0,1,0)
=(11.6,22.1,30)\ \mathrm{mm}.
$$

La conversión sirve para conservar localización física y, en la evaluación futura, medir distancias de bordes. No registra PRE con POST ni demuestra correspondencia anatómica entre ambos estudios.

## 6. Volumen de la máscara candidata

Contamos sus vóxeles:

$$
N=\sum_{z,y,x}M(z,y,x).
$$

Con geometría regular verificada, el volumen físico es:

$$
V_{\mathrm{mm}^3}=N\,dy\,dx\,dz,
\qquad
V_{\mathrm{cm}^3}=\frac{N\,dy\,dx\,dz}{1000}.
$$

El factor 1000 aparece porque `1 cm = 10 mm` y, por tanto, `1 cm³ = 10³ mm³`. Un centímetro cúbico también equivale a un mililitro.

**Ejemplo sintético:** la región tiene 4 000 vóxeles, `dy = dx = 0.8 mm` y `dz = 2.5 mm`.

```text
Volumen de cada vóxel = 0.8 × 0.8 × 2.5 = 1.6 mm³
Volumen de la región  = 4 000 × 1.6 = 6 400 mm³
                      = 6.4 cm³
```

El mismo conteo con `dz = 5 mm` daría `12.8 cm³`. **Comparar solo el número de vóxeles de dos adquisiciones no es comparar su volumen físico.**

La fórmula mide la región incluida en la máscara. Si el algoritmo incluyó un vaso o perdió parte del objetivo, la multiplicación puede ser matemáticamente correcta y la delimitación anatómicamente incorrecta. La precisión del decimal mostrado no representa la precisión clínica.

No se calcula aquí una variación PRE–POST: todavía faltan equivalencia del objetivo, fase, correspondencia y referencia POST revisada.

## 7. Evaluación futura con una referencia independiente

Esta sección define métricas para un **experimento futuro**. No implica que ya existan referencias POST ni que Axivol haya obtenido estos resultados.

Llamaremos `A` al candidato y `B` a la referencia experta. Ambos deben describir el mismo objetivo en coordenadas físicas compatibles. Para contar intersecciones voxel a voxel, además deben estar en la misma rejilla mediante una correspondencia comprobada.

### Dice

$$
\operatorname{Dice}(A,B)=\frac{2|A\cap B|}{|A|+|B|}.
$$

Cuenta cuánto se superponen las regiones respecto a su tamaño combinado. Con regiones no vacías, vale uno para coincidencia perfecta y cero para ausencia de intersección.

### IoU o Jaccard

$$
\operatorname{IoU}(A,B)=\frac{|A\cap B|}{|A\cup B|}
=\frac{|A\cap B|}{|A|+|B|-|A\cap B|}.
$$

Dice e IoU están relacionados:

$$
\operatorname{Dice}=\frac{2\operatorname{IoU}}{1+\operatorname{IoU}}.
$$

Por ello, informar ambos no aporta dos pruebas independientes de calidad. Son dos escalas de la misma información de solapamiento.

**Ejemplo sintético:** `|A| = 1000`, `|B| = 1200` y `|A ∩ B| = 900`:

```text
Dice = 1 800 / 2 200 ≈ 0.818
IoU  =   900 / 1 300 ≈ 0.692
```

No hay un valor universal que convierta automáticamente esos resultados en aceptables para una aplicación clínica. El objetivo, el tamaño, los errores de borde y el uso previsto importan.

### HD95: discrepancia de bordes en milímetros

Dice no explica dónde está el error. Para examinar los bordes, definimos conjuntos `∂A` y `∂B` de vóxeles de frontera. Una convención reproducible propuesta para este proyecto es considerar frontera a los vóxeles de la máscara con al menos un vecino de seis direcciones fuera de ella, incluyendo el exterior de la imagen.

Para cada punto de frontera de `A`, medimos la distancia física al punto más cercano de la frontera de `B`:

$$
d(a,\partial B)=\min_{b\in\partial B}\|P(a)-P(b)\|_2.
$$

Obtenemos los dos conjuntos de distancias dirigidas:

$$
D_{A\to B}=\{d(a,\partial B):a\in\partial A\},\qquad
D_{B\to A}=\{d(b,\partial A):b\in\partial B\}.
$$

La convención propuesta es:

$$
HD95(A,B)=\max\left(Q_{0.95}(D_{A\to B}),\ Q_{0.95}(D_{B\to A})\right),
$$

donde `Q₀.₉₅` es el percentil 95. Al implementarlo deberán fijarse la extracción de frontera, centros de vóxeles frente a superficies, método de interpolación del percentil —por ejemplo, `method="linear"`— y versiones. Aquí cada punto de frontera cuenta por igual; una implementación con ponderación por área de superficie es otra convención y debe identificarse.

**Ejemplo sintético:** si los percentiles dirigidos son `1.2 mm` y `2.4 mm`, esta definición da `HD95 = 2.4 mm`. No significa que ningún punto se aleje más de `2.4 mm`: puede haber errores mayores en la fracción extrema que el percentil deja fuera.

Algunas bibliotecas concatenan las dos listas antes de calcular el percentil. Eso **no siempre coincide** con tomar el máximo de los dos percentiles dirigidos. No se deben mezclar resultados calculados de esas formas sin advertirlo.

Las distancias se expresarán en **milímetros**. Usar distancias entre índices sin considerar el espaciamiento confunde, por ejemplo, un paso de `0.8 mm` dentro de un plano con uno de `5 mm` entre cortes. Para rejillas que requieran posiciones particulares por corte, la fórmula con `P` conserva esa información.

HD95 reduce la influencia de los puntos más extremos, pero también puede ocultar un error localizado que afecte pocos puntos. Debe acompañarse de inspección de fallos, no usarse como certificado de corrección.

### Diferencia de volumen frente a la referencia

$$
e_V=V_A-V_B,\qquad
AE_V=|V_A-V_B|.
$$

Si `V_B > 0`, puede expresarse un error relativo:

$$
RE_V=100\frac{|V_A-V_B|}{V_B}.
$$

Esto compara **dos segmentaciones del mismo estudio**, no dos momentos del tratamiento.

Dos máscaras pueden tener el mismo volumen y estar en sitios distintos. Por eso, un error volumétrico cero no implica Dice uno ni límites correctos.

### Máscaras vacías y casos no evaluables

La implementación futura debe usar una política explícita:

| Situación | Registro propuesto |
| --- | --- |
| Referencia no vacía y candidato vacío | Dice e IoU = 0; fallo de segmentación. HD95 no tiene un valor finito; usar `null` con motivo, sin reemplazarlo por cero. |
| Referencia vacía y candidato no vacío | Dice e IoU = 0; revisar falso positivo o definición de elegibilidad. HD95 y error relativo de volumen no definidos. |
| Ambas vacías | Registrar el caso y el motivo; no asignar automáticamente un acierto perfecto en un estudio de lesiones presentes. |
| Geometría incompatible o correspondencia no verificable | No calcular métricas voxel a voxel hasta resolver la correspondencia. |

Informar solo el promedio de los HD95 finitos ocultaría los fallos excluidos. El reporte debe mostrar cuántos casos fueron evaluables, cuántos fallaron y por qué.

## 8. Ejercicio de lectura del algoritmo

Antes de interpretar un candidato, intenta responder estas preguntas con sus metadatos:

1. ¿De qué caso, estudio, serie y adquisición procede?
2. ¿Cuál es el orden de ejes y cuál es la forma del volumen?
3. ¿Qué semilla, caja e intervalo HU se utilizaron?
4. ¿La máscara toca la caja? ¿Podría estar recortada o conectada a otra estructura?
5. ¿Qué tamaño físico tiene cada vóxel y cómo se calculó `dz`?
6. ¿El volumen corresponde al candidato o a una referencia revisada?
7. ¿Existe una referencia independiente que permita calcular Dice y HD95?

Si la última respuesta es no, podemos comprobar el cálculo y la procedencia, pero todavía no dar una cifra de exactitud anatómica.

Las fuentes científicas y técnicas verificadas están reunidas, con su uso concreto, en [Recursos y referencias](post-segmentation.md#10-recursos-y-referencias-verificadas). SimpleITK y SciPy respaldan la operación de conectividad; DICOM define la geometría; Taha y Hanbury analizan las métricas. Las decisiones específicas de ROI, conectividad seis y protocolo de evaluación pertenecen a este proyecto y deben conservarse explícitas.
