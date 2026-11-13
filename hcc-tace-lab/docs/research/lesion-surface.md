# Superficie 3D de una máscara POST

El visor 3D permite explorar la forma de una **máscara POST guardada**. Usa exactamente sus vóxeles y la geometría física de su adquisición. Girar o acercar la vista no vuelve a segmentar, no modifica archivos y no cambia el volumen registrado. La salida conserva el estado **Unreviewed**.

La superficie ayuda a reconocer continuidad, componentes separadas y escalones entre cortes. No identifica por sí misma tejido tumoral ni confirma que la máscara delimite correctamente el objetivo tratado. La revisión de los bordes debe continuar sobre la TC, corte por corte. No hay una referencia POST independiente disponible para atribuir exactitud anatómica a esta visualización.

## Cómo abrirla

1. Seleccionar el caso y la adquisición de seguimiento.
2. En **Segmentation → Follow-up**, elegir una máscara de **Saved masks**.
3. Pulsar **3D view**. El diálogo muestra el caso, la adquisición, el volumen de esa máscara y los cortes que contienen foreground.
4. Arrastrar para girar y usar la rueda o los botones **− / +** para acercar. Las flechas del teclado giran; **R** y **Reset view** restablecen la cámara. **Anterior**, **Posterior** y **Left** ofrecen direcciones físicas predefinidas.
5. Cerrar con **Close** o Escape para volver a la TC. Cambiar de caso, adquisición o máscara invalida la vista anterior.

Si no existe una máscara guardada, el botón no está disponible. Una máscara vacía produce un estado explícito sin inventar una superficie. Si WebGL2 no está disponible, se informa el problema y la máscara sigue disponible en los visores de TC.

El diálogo utiliza `showModal()`, que coloca el contenido en la capa modal del navegador y vuelve inerte el resto del documento mientras permanece abierto. La implementación restituye el foco al control que lo abrió. [Documentación oficial de `HTMLDialogElement.showModal()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLDialogElement/showModal).

## Qué geometría se representa

La máscara es un arreglo booleano `M[z,y,x]` sobre la retícula nativa POST. El visor representa cada vóxel seleccionado como una caja; solo emite las caras que limitan con un vóxel no seleccionado o con el exterior de la adquisición. Dos vóxeles contiguos por cara no generan una pared interna entre ambos. Cada cara expuesta se divide en dos triángulos y las esquinas coincidentes comparten un índice.

Se conservan todas las componentes, aunque sean pequeñas. También se conservan las paredes de las cavidades internas, aunque puedan quedar ocultas desde una vista exterior. Los contactos entre vóxeles únicamente por una esquina o arista se mantienen: la malla puede no ser una superficie *manifold*. No se aplican operaciones para corregir esa topología.

No hay suavizado de vértices, interpolación de etiquetas, selección de la componente mayor, cierre de huecos ni reducción de resolución. La iluminación es plana por triángulo y no promedia normales para disimular las caras. El aspecto escalonado corresponde a la representación discreta de la máscara; puede ser especialmente visible cuando la separación entre cortes supera el tamaño del píxel en el plano.

### Coordenadas DICOM y milímetros

Para anatomía humana, LPS indica ejes positivos hacia la izquierda del paciente (**L**), posterior (**P**) y superior (**S**). `ImagePositionPatient` sitúa el centro del primer vóxel; `ImageOrientationPatient` aporta las direcciones del plano. `PixelSpacing` expresa primero la separación entre filas y después entre columnas. Estos conceptos proceden del [módulo de plano de imagen DICOM, PS3.3 C.7.6.2](https://dicom.nema.org/medical/dicom/current/output/chtml/part03/sect_C.7.6.2.html), consultado en su edición vigente 2026d.

En la implementación, `o` es el origen LPS, `e_x` y `e_y` son las dos direcciones DICOM y `e_z=e_x×e_y`. El centro del vóxel `(x,y,z)` se ubica mediante:

$$
p(x,y,z)=o+x\,d_x e_x+y\,d_y e_y+z\,d_z e_z.
$$

`d_z` se comprueba a partir de las posiciones ordenadas de los cortes. No se sustituye por el grosor nominal de corte. Se exige separación regular, orden coherente y ausencia de desplazamiento lateral entre planos. Para esta superficie, las posiciones deben estar a un máximo de **0.01 mm** de la retícula uniforme esperada; una geometría incompatible produce un error, sin remuestreo automático.

La extensión de media separación a cada lado del centro es la convención geométrica de este visor para representar las celdas de la retícula. Para una esquina de coordenadas enteras `k=(k_x,k_y,k_z)`:

$$
p_{\mathrm{esquina}}=
o+D\left[(k-\tfrac12)\odot(d_x,d_y,d_z)\right],
\qquad D=[e_x\ e_y\ e_z].
$$

Por ejemplo, un único vóxel con espaciamiento `(d_x,d_y,d_z)=(0.8,0.7,2.5)` mm ocupa una caja de esas dimensiones, con semiextensiones `(0.4,0.35,1.25)` mm respecto de su centro. Produce ocho vértices, seis caras y doce triángulos. La representación no añade precisión subvoxel a su delimitación.

La respuesta conserva posiciones LPS en milímetros. El navegador resta el centro de la caja física antes de convertir a `Float32` para dibujar; esa traslación mejora la precisión numérica local y no cambia proporciones ni distancias. La cámara usa proyección ortográfica con la misma escala física en ambos ejes de pantalla. **Anterior** observa desde −P, **Posterior** desde +P y **Left** desde +L. Las letras L/R, P/A y S/I usan la misma cámara que la superficie.

### Límite de la adquisición

Si la máscara alcanza una cara del volumen adquirido, el helper la registra en `acquisition_touch_faces`: `z_min`, `z_max`, `y_min`, `y_max`, `x_min` o `x_max`. La interfaz avisa **Mask reaches the scan boundary.** La superficie incluye las caras exteriores de esos vóxeles para representar la máscara guardada completa. Ese cierre geométrico no demuestra que el objeto anatómico termine ahí; la adquisición puede tener cobertura incompleta.

### El volumen no se recalcula desde el render

El dato mostrado se comprueba contra el conteo de vóxeles y el espaciado nativo:

$$
V_{\mathrm{cm}^3}=\frac{N\,d_xd_yd_z}{1000}.
$$

La aplicación compara ese valor con el volumen guardado de la candidata antes de responder. La cámara, la iluminación, el tamaño del canvas y la cantidad de triángulos no intervienen en la medición. Una comprobación sintética de volumen cerrado de la malla sirve para detectar errores de geometría o de orientación de triángulos; no se utiliza como nueva medición clínica ni como evaluación de respuesta terapéutica.

## Procedencia e integridad

La ruta de lectura es `GET /api/candidates/{id}/surface`. Acepta una candidata POST y no genera ni guarda otra segmentación.

Antes de construir la superficie, `Lab.candidate_surface` comprueba el identificador, la etapa POST, el caso, la serie, la adquisición y la identidad de la retícula. Verifica la procedencia de las TC y, cuando corresponde a una máscara automática, la de la TC PRE y el SEG publicado usados para localizar el objetivo. Comprueba también el SHA-256 del archivo de máscara actual.

La malla se conserva temporalmente en una caché de hasta dos entradas, cuya clave incluye el identificador, el hash de máscara y la identidad de la retícula. Los controles de integridad se repiten incluso si hay una entrada reutilizable. Cuando se construye una malla nueva, se decodifica el NPZ que se acaba de verificar; no se reutiliza una máscara antigua cacheada únicamente por identificador. Al terminar se vuelven a comprobar archivo, metadatos y fuentes. Se rechaza cualquier discrepancia de forma, conteo o volumen.

La respuesta incluye `candidate_id`, `mask_sha256`, caso, serie, adquisición, `review_status='unreviewed'` y procedencia de localización PRE cuando exista. La máscara PRE no aporta vértices a la superficie POST. El helper geométrico no lee archivos: recibe únicamente la máscara nativa y su `Stack` verificado.

## Ejecución local y límites

El navegador dibuja triángulos en un canvas WebGL2 con código servido por Axivol. No necesita un motor externo, una biblioteca cargada desde una CDN ni otra inferencia de un modelo. La respuesta transmite geometría y metadatos de procedencia; no manda intensidades de la TC al renderizador 3D. [Referencia oficial de `WebGL2RenderingContext`](https://developer.mozilla.org/en-US/docs/Web/API/WebGL2RenderingContext).

La extracción recorta el trabajo a la caja que contiene toda la máscara, sin eliminar foreground. Cuenta las caras antes de reservar sus coordenadas y utiliza un buffer booleano reutilizable. Una operación de construcción se ejecuta a la vez. Los límites de ingeniería son:

| Recurso | Límite del helper |
| --- | ---: |
| Retícula de entrada | 64,000,000 vóxeles |
| Caras expuestas | 120,000 |
| Vértices compartidos | 200,000 |
| Triángulos | 240,000 |
| JSON geométrico serializado | 24 MiB |

Si se supera un límite se devuelve un error explícito. La aplicación no simplifica la máscara ni descarta componentes para poder mostrarla. El límite de JSON corresponde al helper; el endpoint añade los metadatos de la candidata.

### Perfil de desarrollo del 13 de noviembre de 2026

Se verificaron hashes de las TC ordenadas, identidad de retícula y hashes de los artefactos antes de perfilar. En ambos casos la localización usó la etiqueta publicada **Mass**, segmento 2. Estos tiempos corresponden al helper local con NumPy y Python 3.12, incluida su comprobación del presupuesto JSON; excluyen lectura de fuentes, HTTP y dibujo en el navegador.

| Fuente de la máscara | Adquisición POST | Vóxeles | Caras | Vértices | Triángulos | JSON | Tiempo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HCC_006, resultado refinado que corresponde a la candidata actual | 1 | 11,355 | 6,416 | 6,419 | 12,832 | 410,721 bytes | 0.020 s |
| HCC_002, artefacto experimental `refined-2mm.npz` | 1 | 413,118 | 60,718 | 60,692 | 121,436 | 4,270,914 bytes | 0.150 s |

**El segundo renglón no describe una candidata histórica de la app.** Corresponde al artefacto del experimento de sensibilidad de borde, que puede diferir de máscaras guardadas en otras ejecuciones. La localización PRE de HCC_006 utiliza la adquisición 3; la de HCC_002, la adquisición 1. Ninguna de estas dos máscaras toca el límite de su adquisición POST.

El máximo de memoria residente observado en el proceso local al finalizar ambos perfiles fue aproximadamente **206 MiB**. Incluye bibliotecas, arrays y objetos Python acumulados del proceso; no es memoria incremental del helper, ni una medida de la aplicación con el modelo cargado. Estos dos perfiles no garantizan rendimiento para otras geometrías o navegadores y no miden exactitud anatómica.

## Código y comprobaciones

- [Geometría pura: `hcclab/surface.py`](../../hcclab/surface.py).
- [Integración de fuentes y caché: `hcclab/lab.py`](../../hcclab/lab.py).
- [Renderizador y controles: `web/lesion-surface.js`](../../web/lesion-surface.js).
- [Pruebas sintéticas](../../tests/test_surface.py) y [pruebas de integración](../../tests/test_surface_integration.py).

Al incorporar esta documentación, **18 pruebas de helper e integración estaban aprobadas**. Cubren medias distancias de vóxel, espaciado anisotrópico, orientación DICOM rotada, triángulos salientes, supresión exclusiva de caras internas, cavidades, componentes desconectadas, contacto por esquina, límites de adquisición, máscaras vacías, entradas inmutables, geometrías incompatibles, límites de recursos y sustitución coherente de un archivo bajo el mismo identificador sin reutilizar píxeles obsoletos.

### Comprobación integrada final

La aplicación reconstruida pasó **211/211 pruebas de backend en 165.030 s**, con `HCC_PILOT_TEST=1`, incluidas las 18 anteriores y cuatro pruebas de los descriptores por corte. El piloto genera su candidata en almacenamiento temporal; no sustituye las máscaras guardadas de la aplicación.

También pasaron nueve grupos de [pruebas del visor 3D](../../tests/ui_surface.cjs): extracción bajo demanda, coincidencia de fuente/hash/conteo/volumen, render WebGL real, rotación, zoom, presets, teclado, Escape y foco, reapertura, respuesta tardía tras cambiar de caso, errores HTTP, discrepancia de hash, aviso de límite de adquisición y ausencia de WebGL. El aviso se probó alterando exclusivamente una respuesta en el navegador; no se modificó una máscara médica. La inspección de las capturas detectó que el atributo `hidden` del SVG ocultaba los ejes pese a existir sus etiquetas; se corrigió y la prueba final exige visibilidad y dimensiones reales del SVG.

Se ejercitaron las candidatas guardadas HCC_006 `25b9f264bf5b40dfa2dfc54cb4b30f6b` (POST adquisición 1, 11,355 vóxeles y 12,832 triángulos) y HCC_002 `44671dd0300743b190c27d61f5a9014b` (POST adquisición 1, 288,361 vóxeles y 184,868 triángulos). Esta última es una candidata histórica distinta del artefacto HCC_002 perfilado arriba. Ambas usan `Mass` PRE, segmento 2, como localización; las adquisiciones PRE son 3 y 1, respectivamente. Los controles de la vista 3D conservaron las fuentes y los cortes de ambos visores TC.

Los siete grupos existentes de `tests/ui_workspace.cjs` también pasaron, con anchos 1600, 1280 y 390 px. La vista 3D se inspeccionó en escritorio y móvil de 390 px. No hubo errores JavaScript/WebGL ni solicitudes de escritura; las candidatas permanecieron intactas. Evidencias locales: `output/ui-surface-final/validation.json` y capturas de esa carpeta; `output/post-slice-review/workspace-qa/workspace-validation.json`; `output/post-slice-review/backend-tests-20261113.log`. Las imágenes médicas permanecen fuera de Git.

Para repetir desde esta carpeta, con un entorno Node que tenga Playwright y Chromium instalados:

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
AXIVOL_URL=http://127.0.0.1:8770 AXIVOL_QA_DIR=/tmp/axivol-surface-qa node tests/ui_surface.cjs
```

La QA necesita máscaras ya guardadas de ambos casos; no las genera. Estos resultados verifican propiedades del software, no exactitud anatómica. La [revisión de HCC_006 por cortes](hcc006-slice-review.md) registra las irregularidades de la predicción que siguen sin resolver.
