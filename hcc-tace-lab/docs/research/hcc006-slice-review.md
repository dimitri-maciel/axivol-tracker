# Revisión por cortes de la máscara POST HCC_006

**Fecha: 13 de noviembre de 2026. Estado: auditoría técnica y visual de una predicción sin referencia experta.** La revisión identifica irregularidades concretas; no confirma que el área delineada corresponda por completo a la lesión tratada. No se modificó la candidata, no se volvió a ejecutar el modelo y no se generaron métricas de exactitud anatómica.

## Qué se revisó

- Caso HCC_006, TC POST adquisición 1, serie `1.3.6.1.4.1.14519.5.2.1.1706.8374.282486593875087229522187699711`.
- Retícula nativa `[103,512,512]`, separación `[2,5; 0,78125; 0,78125]` mm en orden `[z,y,x]`.
- Máscara actual identificada en la app como `25b9f264bf5b40dfa2dfc54cb4b30f6b`: refinamiento de 2 mm de la pasada `run-20261113T211925-17cd15d6`. La comparación usa la máscara **cruda de esa misma pasada**. La candidata histórica de 11 172 vóxeles pertenece a otra ejecución y no interviene.
- Localización original: PRE adquisición 3, `Mass` segmento 2; `Liver` segmento 1 se usó únicamente como contexto de registro. El objeto POST sigue siendo una propuesta de lesión tratada completa, cuya identidad y extensión necesitan confirmación.

El [script de auditoría](../../experiments/audit_post_slices.py) reutiliza `plot_boundary.read_inputs`: comprueba catálogo, caso/etapa, adquisición, retícula física, SOP ordenados, hashes CT actuales y artefactos. Repite las comprobaciones al terminar. No importa SimpleITK ni el predictor. Los SHA-256 de los NPZ son:

| Entrada | SHA-256 |
| --- | --- |
| Cruda, `raw.npz` | `df6857f9de5c5fd1496cbf36bd450292e4e0f881efe270b27b371969ec74c142` |
| Actual, `refined-2mm.npz` | `511fcc15336070f0aae794e418e97edcea5af7e5fca0188b60e2217c813bf940` |

Se calcularon descriptores de **los 103 cortes**, incluidos los 87 sin máscara. Se inspeccionaron visualmente los 16 cortes ocupados y tres vecinos a cada extremo: índices de array 46–67, equivalentes a **cortes 47–68 de la app**. No se afirma haber hecho una lectura anatómica completa de los otros 81 cortes.

## Resultado principal

**El refinamiento cambia el borde, pero conserva una prolongación gris alargada cuya pertenencia al objetivo no está resuelta.** Es visible hacia abajo e izquierda del acercamiento —direcciones de la imagen, no una denominación anatómica— en varios cortes. Se reduce mucho en el corte 55 de la app y reaparece en el 56. Ese cambio ya aparece en la máscara cruda; el refinamiento de 2 mm no lo corrige.

Entre los cortes de la app 55 y 56 (`z=54→55`) el centroide se desplaza **6,958 mm en el plano**. Entre 54 y 55 (`z=53→54`) se desplaza 5,237 mm. Son distancias entre centroides de máscaras consecutivas, sin incluir los 2,5 mm entre cortes. Pueden reflejar cambios de forma, adquisición o predicción; no son desplazamiento de una lesión entre estudios ni prueba de error anatómico.

El corte 60 (`z=59`) conserva **cuatro componentes con conectividad 4** y un hueco interno de dos píxeles; los cortes 62 y 63 (`z=61,62`) tienen un hueco de un píxel cada uno. Estas pequeñas irregularidades justifican revisión técnica, pero no autorizan a rellenar o eliminar automáticamente regiones. Una componente separada en 2D puede estar conectada a otra en 3D.

La máscara cruda tiene 11 427 vóxeles y la actual 11 355. Se añaden 167 y se retiran 239: **406 etiquetas cambiadas, 3,553 % del recuento crudo**, con una diferencia neta de −72 vóxeles. El refinamiento es pequeño en relación con la selección inicial del objetivo. No demuestra que se haya incluido todo lo que corresponde ni excluido todo lo que no corresponde.

## Lectura visual de cada corte ocupado

La prioridad ordena la **revisión de la máscara**, no expresa urgencia clínica. «Alta» señala extremos, discontinuidades, prolongaciones ambiguas o irregularidades que deben resolverse antes de aceptar el contorno. Todas las identidades y delimitaciones anatómicas siguen sin verificar. Área en mm²; `+ / −` son píxeles añadidos/retirados respecto a la máscara cruda.

| Corte app (`z` array) | Píxeles; área | + / − | Observación directa | Pregunta no resuelta / prioridad |
| --- | ---: | ---: | --- | --- |
| 50 (49) | 279; 170,3 | 1 / 16 | Primera máscara: región oblicua pequeña, centro más claro y extremo gris. | ¿Es el primer plano del objetivo o falta extensión previa? **Alta**. |
| 51 (50) | 454; 277,1 | 10 / 9 | Región mayor, centro muy brillante y pequeña extensión gris hacia abajo e izquierda. | Determinar si esa extensión pertenece al objetivo completo. Media. |
| 52 (51) | 487; 297,2 | 6 / 14 | Contorno relativamente compacto alrededor de una región brillante; borde gris estrecho. | El brillo dominante no define por sí solo el objetivo. Media. |
| 53 (52) | 601; 366,8 | 10 / 17 | Aparece una prolongación gris marcada hacia abajo e izquierda, con unión estrecha al cuerpo brillante. | Distinguir extensión del objetivo de seguimiento de una estructura contigua. **Alta**. |
| 54 (53) | 635; 387,6 | 5 / 13 | Persiste una región irregular gris inferior, con entrante junto a una franja más oscura. | Revisar la transición hacia el corte siguiente. **Alta**. |
| 55 (54) | 462; 282,0 | 7 / 10 | La prolongación larga casi desaparece y la máscara se concentra en la región brillante. | La contracción brusca no queda explicada por el ajuste de borde. **Alta**. |
| 56 (55) | 709; 432,7 | 9 / 11 | Reaparece una prolongación larga; el centroide cambia 6,958 mm en plano. | Resolver esta alternancia con 55 y 57, no suavizarla a ciegas. **Alta**. |
| 57 (56) | 901; 549,9 | 8 / 10 | La máscara incluye un extremo gris alargado y un cuerpo brillante; cambia poco frente a la cruda. | La banda conserva la decisión inicial sobre ese extremo. **Alta**. |
| 58 (57) | 935; 570,7 | 15 / 10 | Se mantiene la prolongación, separada visualmente del cuerpo brillante por un cuello más estrecho. | Verificar continuidad e identidad del tejido incluido. **Alta**. |
| 59 (58) | 1 032; 629,9 | 7 / 19 | Región amplia, con entrantes en la unión del extremo gris al área brillante. | El contraste local no permite certificar por sí solo esa unión. **Alta**. |
| 60 (59) | 1 028; 627,4 | 17 / 45 | Se observan pequeños puntos separados; el recuento confirma cuatro componentes y un hueco. | Revisar cada componente y su continuidad 3D antes de cualquier filtro. **Alta**. |
| 61 (60) | 972; 593,3 | 19 / 8 | Una componente en este plano; persiste la extensión gris. Parte del centro continúa blanca con ventana amplia. | La ventana amplia no elimina toda la saturación ni valida el borde. **Alta**. |
| 62 (61) | 1 055; 643,9 | 13 / 21 | Mayor área de esta máscara; extremo gris amplio con tramo de borde casi horizontal. Un hueco de un píxel. | Revisar si ese borde sigue una transición real o la forma de la predicción. **Alta**. |
| 63 (62) | 990; 604,2 | 26 / 21 | Contorno con entrante inferior y una pequeña adición localizada; persiste un hueco de un píxel. | No interpretar automáticamente la adición como tejido recuperado. **Alta**. |
| 64 (63) | 527; 321,7 | 9 / 8 | Disminuyen el cuerpo brillante y la longitud de la prolongación. | Confirmar transición de salida del objetivo en las vistas ortogonales. **Alta**. |
| 65 (64) | 288; 175,8 | 5 / 7 | Último plano ocupado: región pequeña predominantemente gris y centro claro tenue. | Confirmar el extremo real; el siguiente plano carece de máscara. **Alta**. |

En los vecinos **47–49 de la app** (`z=46–48`) y **66–68** (`z=65–67`) no hay máscara cruda ni refinada. Se mostraron como contexto con idéntico acercamiento. No se etiquetan como «sin lesión»: ausencia de predicción no es una anotación de ausencia, y esta auditoría no establece qué tejido debería segmentarse allí.

## Cómo interpretar las imágenes y los descriptores

Las cinco páginas muestran TC completa con recuadro de localización, acercamiento sin máscara, contorno actual, contorno crudo, cambios y contorno actual con segunda ventana. La primera ventana es W400/L40 (`−160…240 HU`); la segunda W800/L200 (`−200…600 HU`). El acercamiento conserva un margen fijo de 20 mm y la misma posición en todos los planos. Los contornos son el borde **interno exacto de un píxel**, sin interpolación ni suavizado; las barras miden 10 mm. No se ha vuelto a inferir con la segunda ventana.

El **52,224 %** de los vóxeles de la máscara actual alcanza o supera 240 HU, y el **34,725 %** alcanza o supera 600 HU. Esos píxeles llegan al blanco de cada ventana y pierden distinción visual entre intensidades altas. Son estadísticas del display dentro de una predicción, **no una clasificación de Lipiodol, viabilidad o necrosis**. El campo visual amplio ayuda a reconocer la saturación, pero no convierte la intensidad en referencia anatómica.

`slices.csv` y `slices.json` conservan conteos y áreas, componentes 4, huecos con conectividad de fondo 8, centroides, salto de centroide consecutivo, cambios, estadísticas HU y gradiente del borde en HU/mm. Los cortes vacíos tienen estadísticas no definidas como `null`/celda vacía, no ceros inventados. El gradiente usa diferencias finitas en plano con `dy,dx`, sin suavizado, y excluye los bordes sin vecinos válidos por padding. Una cifra alta puede corresponder a un borde interno brillante; una baja puede corresponder a una transición real poco contrastada. **No se usa un umbral clínico para aprobar o rechazar un corte.**

La máscara tiene ceros fuera de su extensión y del campo de inferencia. La auditoría de fuentes y la [comparación de bandas](post-boundary-refinement.md#7-resultados-locales-de-desarrollo) comprobaron ausencia de foreground en padding, fuera del campo o fuera de la ROI. Estas restricciones de cálculo no demuestran ausencia de lesión fuera de esas regiones.

## Qué mejorar y qué no cambiar todavía

1. **Resolver la extensión gris y la alternancia de los cortes 54–56 de la app.** La revisión debe comparar la misma región en axial, coronal y sagital y confirmar la identidad del objetivo con el PRE. No se debe borrar esa prolongación solo porque sea menos brillante, ni conservarla solo porque esté conectada.
2. **Revisar extremos y puntos separados.** Priorizar cortes 50, 60 y 65, y sus vecinos. Los componentes y huecos son señales para inspeccionar, no reglas para modificar la máscara.
3. **Separar selección del objetivo y ajuste del borde.** La banda de 2 mm congela gran parte de la predicción. Con `dz=2,5 mm`, no permite cambiar un límite plano entre cortes cuando el centro de clase opuesta más próximo está a 2,5 mm. No puede reparar por sí sola una selección inicial incorrecta o una extensión extensa fuera de banda.
4. **Si se ensayan cambios del algoritmo, conservar comparadores.** Localización/prompt, ventana y logits débiles pueden estudiarse por separado en experimentos reproducibles. No escoger la combinación que produzca el contorno visualmente preferido en este único caso ni erosionar automáticamente para hacerlo más compacto.
5. **Para hablar de mejora real, obtener una referencia independiente.** El [paquete de anotación](annotation-kit.md) y el [protocolo de validación](post-tace-validation.md) distinguen delineación experta independiente de corrección de la salida del modelo. Aquí no se calculó Dice/HD95 frente a un experto y no se marcó la candidata como revisada.

## Archivos, reproducibilidad y pruebas

Artefactos locales finales, no destinados a Git: `output/post-slice-review/HCC006-25b9-20261113-final/`, con `page-01.png` a `page-05.png`, `slice-review.pdf`, `slices.csv`, `slices.json` y `manifest.json`. El manifiesto registra hashes del código lector/auditor, fuentes, máscaras y salidas, versión de dependencias, ventanas y convenciones de cálculo. La primera carpeta sin sufijo `-final` conserva el borrador previo al ajuste de espacio del encabezado; las métricas son las mismas.

Desde la raíz del espacio de trabajo, con el Python 3.12 que corresponde a `.deps`:

```bash
MPLCONFIGDIR="$PWD/tmp/matplotlib" \
PYTHONPATH=.deps:hcc-tace-lab \
/Users/prueba/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m experiments.audit_post_slices \
  --data hcc-tace-lab/data \
  --run output/post-boundary/run-20261113T211925-17cd15d6 \
  --output output/post-slice-review/HCC006-new-review
```

La ruta de Python es la del equipo donde se ejecutó; en otro entorno se utiliza un intérprete compatible con NumPy, SciPy, pydicom y Matplotlib. El directorio de salida debe ser nuevo: el script rechaza sobrescrituras y escritura dentro de datos, código o la pasada original. Las cuatro [pruebas sintéticas](../../tests/test_slice_audit.py) comprobaron área anisótropa, salto de centroide sin puente sobre cortes vacíos, componentes/huecos, gradiente físico, padding, conservación de entradas y selección completa de planos. Pasaron localmente en Python 3.12 y en `docker compose run` usando la imagen `app`, con experimentos y pruebas montados en solo lectura. La inspección visual se realizó página por página; no es validación radiológica.

Se aplicaron `scientific-visualization` y `karpathy-guidelines`; el [crédito de Scientific Agent Skills](README.md#herramientas-de-apoyo-y-crédito) documenta ese apoyo procedimental, sin atribuirle validez anatómica.
