# Investigación POST: por dónde empezar

Este espacio explica cómo investigar la delimitación de una lesión después de TACE y cómo comprobarla. **Una máscara calculada sigue siendo una candidata sin revisar; no es una referencia experta ni una medida de respuesta al tratamiento.**

## Recorrido recomendado

| Orden | Documento | Pregunta que responde |
| --- | --- | --- |
| 1 | [Guía matemática](post-mathematics.md) | ¿Cómo pasan los píxeles DICOM a coordenadas, una localización POST y un volumen? |
| 2 | [Evidencia y decisiones](post-tace-evidence.md) | ¿Qué tejido queremos delimitar, qué métodos ayudan y dónde falla la evidencia disponible? |
| 3 | [Resultados de desarrollo](../../experiments/ABLATION.md) | ¿Qué ocurrió con HCC_006 y el control HCC_002 al cambiar contexto, ventana y registro? |
| 4 | [Reproducir los experimentos](../../experiments/README.md) | ¿Cómo ejecutar el CLI y leer su manifiesto, máscaras y diagnósticos? |
| 5 | [Modelos y comparadores](post-tace-models.md) | ¿Qué aportaron nnInteractive y TotalSegmentator y cuáles son sus costes y límites? |
| 6 | [Referencia y validación](post-tace-validation.md) | ¿Cómo conseguir anotaciones independientes y evaluar sin contaminar la prueba? |
| 6a | [Paquete de anotación](annotation-kit.md) y [comandos de preparación y evaluación](../../experiments/README.md#referencia-post-independiente) | ¿Cómo entregar las imágenes a un lector sin mostrar la predicción y comparar después su referencia? |
| 7 | [Revisión experimental](code-review-experiments.md) y [revisión de integración](code-review-runtime.md) | ¿Qué se revisó, qué defectos se corrigieron y qué comprobaciones quedan? |

Para explicar el proyecto oralmente, conviene empezar por el objetivo de la lesión completa, describir el fallo de identidad y terminar con la comparación experimental y la referencia experta que falta. Los símbolos y las fórmulas sirven para entender el procedimiento, no para sustituir esa explicación.

## Aplicación, experimentos y validación

Estado al redactar este índice, 12 de noviembre de 2026:

| Estado | Contenido | Documento de referencia |
| --- | --- | --- |
| Aplicación desplegada localmente, protocolo 2.0.0 | Registro rígido global → refinamiento con `Liver ∪ Mass` originales PRE → localización de `Mass` → EfficientMedSAM2 tiny sobre un campo XY POST con mínimo solicitado de 160 mm, ajustado a la imagen disponible. Procesa todos los cortes z, aplica controles de rechazo y conserva salida `unreviewed`. | [Método automático implementado](../automatic-post-segmentation.md). |
| Baseline semiautomático | Semilla POST, región, intervalo HU y conectividad. Tiene matemáticas y supuestos propios. | [Guía](../post-segmentation.md) y [matemáticas del crecimiento](../post-segmentation-math.md). |
| Experimentos separados | Campo completo, otros contextos/ventanas y comparadores 3D. El refinamiento y cálculo de campo comparten helpers con la app; las salidas de investigación se guardan aparte. nnInteractive y TotalSegmentator no son un fallback de la aplicación. | [Runner](../../experiments/README.md), [ablaciones](../../experiments/ABLATION.md) y [comparadores](post-tace-models.md). |
| Arquitectura integrada | Contratos y límites de la ruta adoptada. | [Arquitectura](../../experiments/ARCHITECTURE.md). |
| Validación pendiente | Referencias POST independientes, lectores y prueba en pacientes no utilizados para desarrollar el método. | [Protocolo](post-tace-validation.md). |

La suite integrada ejecutada con `docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v` completó **165/165 pruebas en 133,234 s**, incluido el piloto HCC_002. Las comprobaciones de interfaz de HCC_002 y HCC_006 también pasaron: generación, estado sin revisar, visibilidad, persistencia y navegación. La ampliación incluye 31 pruebas sintéticas de preparación, evaluación y geometría de anotación. Slicer no se ejecutó gráficamente. Estos resultados comprueban funcionamiento y contratos; no demuestran exactitud del contorno.

Los resultados de experimentos no sustituyen automáticamente sus candidatas ni los segmentos publicados. Para saber qué versión se ejecutó, consultar el manifiesto y sus hashes, además del nombre del método. Las máscaras históricas del protocolo anterior conservan su propia versión y procedencia.

## Cómo interpretar los resultados

- **Geometría válida:** las coordenadas y fuentes corresponden. No demuestra que el contorno sea anatómicamente correcto.
- **Consistencia:** dos predicciones se parecen o una máscara queda cerca de la localización esperada. Pueden compartir el mismo error.
- **Exactitud anatómica:** exige comparación con una referencia independiente cuyo objetivo esté definido.
- **Utilidad:** además requiere medir revisión/corrección y sus límites; el tiempo CPU no equivale a tiempo clínico ahorrado.

HCC_002 y HCC_006 son casos de desarrollo. El protocolo propone comenzar con seis pacientes nuevos para preparar referencias y medir esfuerzo; ese piloto tampoco será una prueba independiente del método finalmente ajustado.

## Herramientas de apoyo y crédito

Se utilizaron las skills `academic-research-suite`, `design-study` y `model-assessment` para búsqueda y diseño; `differential-review` y `karpathy-guidelines` para revisión del código; y `pydicom` como apoyo al manejo documentado de DICOM. Las skills son instrucciones de trabajo, no modelos de segmentación ni evidencia de exactitud.

Como crédito de estas herramientas de procedimiento: Kassis, T.; Agarwal, V.; He, Y.; Patel, D.; y Brueckner, A. M. (2026), *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. [Preprint](https://arxiv.org/abs/2609.00065), [DOI 10.48550/arXiv.2609.00065](https://doi.org/10.48550/arXiv.2609.00065). La versión consultada es v2; no se atribuye un DOI de revista ni una validación del algoritmo a este recurso.
