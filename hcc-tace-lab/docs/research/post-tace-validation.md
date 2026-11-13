# Protocolo propuesto de referencia y validación POST

Versión de planificación: 12 de noviembre de 2026. Este documento especifica trabajo pendiente. **No existen todavía en este proyecto referencias POST expertas independientes con las que afirmar exactitud del algoritmo.** La revisión que fundamenta las decisiones está en [post-tace-evidence.md](post-tace-evidence.md).

## 1. Pregunta y alcance

**Pregunta principal:** dado un segmento tumoral PRE publicado y una TC POST del mismo paciente, ¿una adaptación con contexto local y controles de identidad reduce los fallos de correspondencia respecto de la propagación de campo completo, manteniendo una delimitación POST evaluable?

El objetivo es la lesión tratada completa correspondiente a PRE `Mass`; su definición POST requiere especialistas. [La revisión](post-tace-evidence.md#2-qué-queremos-delimitar) distingue este objeto de tejido viable y depósito de Lipiodol. No se evalúa respuesta terapéutica.

La unidad principal es **paciente–lesión–par temporal**. Para un primer estudio acotado se propone una lesión índice y el primer POST elegible por paciente. La regla de selección debe fijarse antes de conocer resultados del modelo. Si después se incluyen más lesiones o seguimientos, permanecerán agrupados por paciente en todas las particiones y análisis.

## 2. Qué es automático y qué no

En el brazo automático la entrada permitida es la TC PRE, su segmento publicado seleccionado y la TC POST elegible. El centro, la caja y el contexto se obtienen por el procedimiento de registro fijado. No se utilizan la máscara de referencia POST, sus extremos, su mejor corte ni correcciones de un operador para generar la salida.

Una caja POST trazada por un experto puede servir como **experimento diagnóstico separado**: permite preguntar cuánto del fallo procede de localizar mal y cuánto de delinear mal. No debe mezclarse con el resultado automático. Lo mismo aplica a un brazo asistido con puntos, pincel o extremos z manuales; deberá reportar intervención y tiempo.

## 3. Cohorte y manifiesto antes de ejecutar el modelo

Crear una tabla de elegibilidad con una fila por paciente y motivo de cada exclusión. No seleccionar solo casos en los que ya se vio una buena máscara. Incluir variedad de tamaños, posición periférica, contraste, depósito radiopaco, espaciado y calidad siempre que el objetivo pueda evaluarse.

Campos mínimos:

| Grupo | Campos |
| --- | --- |
| Identidad | Identificador público, identificador estable de la lesión y par temporal. |
| Imagen | StudyInstanceUID, SeriesInstanceUID, adquisición concreta, lista/hash de SOPInstanceUID, fase y cómo se verificó. |
| Geometría | Forma ZYX, espaciado, orientación, posiciones, cobertura anatómica y validez de los cortes. |
| Referencia PRE | SOPInstanceUID del SEG, número y nombre del segmento, hash de archivo, correspondencias con CT. |
| Temporalidad | Orden PRE/POST y, cuando esté disponible, intervalo respecto de TACE y de la primera TC. No inventar una fecha clínica a partir del orden de archivos. |
| Calidad | Mezcla de fases, referencias faltantes, artefactos, cobertura incompleta y condición evaluable/no evaluable. |
| Partición | Desarrollo, ajuste de controles o prueba bloqueada; fecha y motivo de asignación. |

Las series con adquisiciones mezcladas no deben tratarse como un volumen homogéneo. El artículo del dataset describe ese problema en parte de la colección. Una imagen con geometría válida todavía puede tener fase o contenido clínico inadecuado. [HCC-TACE-Seg](https://pmc.ncbi.nlm.nih.gov/articles/PMC9849450/).

Los motivos de no evaluabilidad se registran **antes** de observar la predicción cuando dependen solo de la imagen. Un fallo de registro, de carga o de inferencia se registra como resultado del sistema, no como exclusión retrospectiva del paciente.

## 4. Separación de datos y prevención de contaminación

1. **HCC_002 y HCC_006 quedan en desarrollo.** Se usaron para elegir parámetros y controles, incluido el rechazo por extensión fuera de la región localizada. Sus resultados no estiman generalización.
2. Todos los cortes, fases, adquisiciones, lesiones y tiempos del mismo paciente van a la misma partición. Una división aleatoria de cortes produciría contaminación.
3. Reservar pacientes nuevos para ajustar el protocolo de anotación y los controles. No usar la futura prueba bloqueada para escoger ventana, tamaño del contexto, umbral de abstención o checkpoint.
4. Auditar el corpus de entrenamiento y ajuste del modelo reutilizado. Una partición local limpia no demuestra independencia respecto de su preentrenamiento. Si el solapamiento con HCC-TACE-Seg no puede resolverse, declararlo y evitar llamar «validación externa independiente» al resultado.
5. Congelar código, pesos, preprocesamiento, criterios y análisis antes de abrir resultados de prueba. Si el test obliga a corregir el método, pasa a formar parte del desarrollo y se necesita una nueva evaluación.

El tamaño de prueba se fijará después del piloto según disponibilidad de referencias y precisión buscada. No justificar una división «80/20» ni potencia estadística sin conocer desenlace y variabilidad. Una muestra pequeña exige un alcance exploratorio.

## 5. Cómo construir la referencia POST

### 5.1 Manual de anotación

Antes de medir rendimiento, un radiólogo con experiencia hepática debe acordar por escrito:

- Qué incluye el contorno de lesión tratada completa; cómo tratar necrosis, depósito de Lipiodol, cápsula dudosa, vasos contiguos y extensión extrahepática.
- Qué adquisición será la rejilla de referencia y qué fases adicionales podrán consultarse. Consultar otra fase no permite copiar sus píxeles sin registro.
- Cómo identificar la misma lesión entre PRE y POST, especialmente cuando hay lesiones vecinas, cambios importantes o mala visualización.
- Qué se considera **presente y delimitable**, **presente con límite incierto**, **no identificable** o **ausencia confirmada por el protocolo experto**. La abstención del algoritmo no implica ausencia de tumor.

Si no hay acuerdo sobre el límite, conservar esa incertidumbre y no fabricar un contorno exacto. Puede definirse una región segura y otra incierta para análisis de sensibilidad, siempre que esa decisión se tome antes de ver resultados de prueba.

### 5.2 Lectores y adjudicación

El diseño preferido contempla dos lectores que delimiten POST independientemente, con experiencia y rol documentados, y un adjudicador para desacuerdos. **Su participación todavía debe conseguirse; no se presume disponible.** Si los recursos solo permiten un lector con una segunda revisión, se debe declarar la limitación y conservar una submuestra de doble anotación independiente.

Para evitar que la predicción determine su propia referencia, los lectores no ven las máscaras de Axivol ni qué variante se está evaluando. Pueden consultar PRE y la información necesaria para identificar el objetivo, pero trazan el contorno en POST. Las herramientas de anotación y operaciones de interpolación/suavizado deben registrarse y revisarse en axial, coronal y sagital. El resultado no se acepta únicamente porque un software haya unido varios contornos.

Guardar por separado las anotaciones originales de cada lector, la versión adjudicada y el registro de cambios. Una combinación automática —incluido STAPLE— no resuelve por sí misma que los lectores hayan delimitado objetivos diferentes. Si se utiliza una máscara generada por otro algoritmo como ayuda, documentarlo y evaluar su posible sesgo de anclaje.

### 5.3 Productos mínimos

- Máscara binaria POST en geometría nativa, con lesión e imagen de referencia inequívocas.
- Formulario de identidad, fase, visibilidad, dificultad y confianza del lector en su delineación; la confianza no reemplaza el contorno.
- Proveniencia: fecha, lector seudonimizado, versión de herramienta, hash de imagen y anotación, reglas aplicadas y adjudicación.
- Estado de revisión separado del estado `unreviewed` de las candidatas automáticas.

## 6. Brazos y ablaciones

Comparar pares de resultados sobre los mismos pacientes. Cada cambio tiene que responder a una pregunta concreta.

| Brazo propuesto | Qué cambia | Qué debe permanecer fijo |
| --- | --- | --- |
| A. Referencia de desarrollo del sistema | Modelo actual con campo transversal completo, prompt localizado y propagación completa. | Peso, registro, intensidad, umbral de máscara y definición del objetivo. |
| B. Contexto XY local | Región física de entrada centrada en la localización; todos los cortes POST. | Mismo checkpoint y mismo origen del prompt. Registrar rescalado y bordes. |
| C. B + control anatómico | Diagnóstico de relación con hígado/órganos vecinos; política de abstención predefinida. | Misma predicción de B antes del control, para separar calidad de máscara de selección de salidas. |
| D. Consistencia, si es viable | Retorno al corte inicial o perturbaciones predefinidas. | Protocolo fijo; no escoger por paciente la mejor máscara tras inspeccionar la referencia. |
| E. Prompt POST experto, diagnóstico | Localización suministrada independientemente por un lector. | Segmentador idéntico. Reportar como asistido, no automático. |

La comparación mínima es A frente a B. Agregar otros brazos solo si permiten atribuirles un beneficio. Un modelo 3D adicional debe usar el mismo origen automático del prompt y declarar diferencias en la información que recibe. La [matriz de evidencia](post-tace-evidence.md#4-matriz-crítica-de-fuentes) explica los precedentes y sus límites.

## 7. Medir registro, identidad y contorno por separado

### Registro

Con `fixed=PRE` y `moving=POST`, la transformación del registro utilizado mapea puntos físicos PRE hacia POST. Para puntos anatómicos correspondientes marcados por un experto y no utilizados para ajustar el registro:

\[
TRE_i = \|T(p_i^{PRE})-p_i^{POST}\|_2\quad[\mathrm{mm}].
\]

Registrar error cerca de la lesión, además de un conjunto anatómico distribuido. Mejorar la métrica de información mutua no garantiza disminuir TRE. Una transformación rígida puede ser insuficiente ante deformación respiratoria; deformar más libremente tampoco garantiza correspondencia. Verificar dirección con pruebas físicas y visualizar superposición anatómica sin aceptar automáticamente el resultado. [Entorno reproducible de registro SimpleITK](https://pmc.ncbi.nlm.nih.gov/articles/PMC5959828/).

### Identidad

El lector clasifica si la salida corresponde a la lesión índice, a otra estructura, a una mezcla o si no puede decidirse. Registrar también si el prompt localizado cae en la lesión esperada. Una componente conexa no acredita identidad: el fallo de desarrollo HCC_006 lo demuestra.

### Contorno y volumen

Sean \(P\) la candidata y \(G\) la referencia POST, sobre la misma rejilla:

\[
Dice(P,G)=\frac{2|P\cap G|}{|P|+|G|}.
\]

Usar también **HD95 en milímetros**. Definir la implementación antes del análisis; por ejemplo, calcular las distancias de cada superficie a la otra y tomar el máximo de los percentiles 95 dirigidos. No confundir esta convención con agrupar todas las distancias, que puede dar otro número. Incluir espaciado físico anisótropo. Guardar versiones de librería y pruebas de casos sintéticos conocidos.

\[
V(P)=|P|\frac{\Delta z\,\Delta y\,\Delta x}{1000}\quad[\mathrm{cm}^3],
\qquad e_V=V(P)-V(G).
\]

Reportar sesgo firmado, error absoluto y, solo cuando \(V(G)>0\), error relativo \(100|e_V|/V(G)\). Son errores respecto de una referencia POST; **no representan respuesta al tratamiento**. Si se evalúa acuerdo volumétrico, un gráfico de diferencias frente a medias ayuda a detectar sesgo por tamaño. No sustituirlo por correlación: dos métodos pueden correlacionarse y discrepar sistemáticamente.

Dice, distancias y volumen describen aspectos diferentes. Preespecificarlos y respetar la jerarquía por paciente evita seleccionar a posteriori la métrica que favorezca una variante. [Taha y Hanbury](https://pmc.ncbi.nlm.nih.gov/articles/PMC4533825/), [Metrics Reloaded](https://www.nature.com/articles/s41592-023-02151-z).

## 8. Abstenciones, vacíos y denominadores

El desenlace principal propuesto es la **proporción de fallos de identidad entre todos los pares elegibles intentados**, junto con la cobertura del sistema. Los resultados de contorno son desenlaces complementarios. Antes de bloquear el estudio debe acordarse si se prioriza esta proporción o un desenlace de segmentación; no cambiarlo al ver los resultados.

\[
Cobertura=\frac{\text{candidatas emitidas}}{\text{pares elegibles intentados}}.
\]

Informar por separado: lesión correcta emitida, estructura incorrecta emitida, abstención, fallo técnico y caso no evaluable por la referencia. No eliminar del informe pacientes difíciles ni presentar un Dice calculado solo sobre casos aceptados sin mostrar cuántos se rechazaron.

Convenciones propuestas que se deben congelar:

| Situación | Tratamiento |
| --- | --- |
| Referencia no vacía y predicción vacía | Dice 0; HD95 no finito/no definido, con conteo explícito. No convertirlo en 0 mm. |
| Referencia vacía confirmada y predicción no vacía | Falso positivo de presencia; volumen espurio. HD95 no definido. |
| Ambas vacías, con ausencia confirmada | Concordancia de presencia/ausencia; no mezclar automáticamente un Dice 1 con los casos de lesión delineable. |
| Abstención | Resultado sin candidata; entra en cobertura y fallos operativos. Reportar calidad condicional de candidatas por separado. |
| Referencia incierta o no identificable | Categoría previamente definida; no usar al algoritmo para resolverla. Reportar cantidad, motivos y análisis de sensibilidad si procede. |

Para una lectura de extremo a extremo, puede añadirse una puntuación secundaria que asigne 0 a abstenciones/fallos con referencia no vacía. Debe llamarse puntuación operacional definida por el estudio, no Dice de una máscara que nunca se generó. Siempre conservar el reporte separado de cobertura y calidad.

## 9. Incertidumbre y esfuerzo humano

Resumir las métricas por paciente/lesión con distribuciones y casos extremos, no solo una media. Para comparar A y B, usar diferencias emparejadas; si se calculan intervalos por bootstrap, remuestrear pacientes completos y conservar sus tiempos/lesiones agrupados. Documentar semilla, número de remuestreos e intervalos. No tratar cientos de cortes como cientos de pacientes.

Medir acuerdo entre lectores antes de adjudicar para conocer la variabilidad de referencia. No interpretar automáticamente ese acuerdo como un techo absoluto para cualquier algoritmo, ni atribuir al modelo errores de una referencia defectuosa.

El ahorro de trabajo requiere otro experimento: delimitar desde cero frente a corregir una candidata, con orden aleatorio, casos contrabalanceados y una estrategia para evitar recuerdo. Medir tiempo total, interacciones y calidad final respecto de una referencia independiente. El tiempo de inferencia CPU por sí solo no mide ahorro de anotación.

## 10. Reproducibilidad y criterios para continuar

El [método implementado](../automatic-post-segmentation.md) define procedencia, geometría nativa y exportación. Registrar además versión de experimento, parámetros, partición, tiempo, memoria y todos los fallos. Los originales permanecen de solo lectura y los datos/pesos fuera de Git. Las pruebas geométricas y de software no sustituyen revisión anatómica.

Se puede avanzar de piloto a evaluación cuando estén disponibles: manual de anotación acordado, lectores, manifiesto y particiones, referencias POST independientes, código/parámetros congelados y plan de análisis firmado o versionado. Hasta entonces se informarán **resultados de desarrollo y análisis de fallos**, sin porcentajes inventados de exactitud ni afirmaciones de uso clínico.

## 11. Próximo estudio mínimo realizable

**Propuesta de piloto: seis pacientes nuevos para desarrollo**, además de 002/006 como casos de fallo conocidos. Es un presupuesto de anotación inicial, no un tamaño suficiente para probar eficacia ni una estimación de potencia. Seleccionar casos antes de ver predicciones, buscando diversidad de tamaño, posición periférica y depósito radiopaco.

1. Un radiólogo hepático revisa la definición de lesión tratada y anota los seis POST sin ver candidatas. Anotar primero dos permite medir esfuerzo y corregir el manual antes de continuar.
2. Un segundo lector delimita tres de los seis de forma independiente, escogidos previamente con dificultad variada, y revisa identidad/contorno en los restantes. Conservar qué casos tienen doble delineación ciega y cuáles solo segunda revisión. Son nueve delineaciones iniciales, más revisión/adjudicación; no se presume un tiempo por caso.
3. Registrar casos no delimitables y discrepancias de objetivo. Resolver el manual antes de ajustar modelos; si se modifica, revisar las anotaciones afectadas.
4. Comparar A/B y, si está listo, un comparador 3D. Analizar identidad, Dice, HD95, volumen, abstenciones y sensibilidad. Los seis siguen siendo desarrollo, incluso si los resultados parecen buenos.
5. Con el esfuerzo y variabilidad observados, fijar número de pacientes nuevos para prueba, criterios y presupuesto experto. Bloquear el método antes de esa prueba.

La disponibilidad de estos lectores es pendiente. Hasta conseguirla se puede mejorar ingeniería y documentar fallos, pero ninguna candidata automática debe convertirse en su propia referencia.
