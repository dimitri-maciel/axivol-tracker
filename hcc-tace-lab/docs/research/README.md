# Investigación POST: por dónde empezar

Este espacio explica cómo investigar la delimitación de una lesión después de TACE y cómo comprobarla. **Una máscara calculada sigue siendo una candidata sin revisar; no es una referencia experta ni una medida de respuesta al tratamiento.**

## Recorrido recomendado

| Orden | Documento | Pregunta que responde |
| --- | --- | --- |
| 1 | [Guía matemática](post-mathematics.md) | ¿Cómo pasan los píxeles DICOM a coordenadas, una localización POST y un volumen? |
| 2 | [Evidencia y decisiones](post-tace-evidence.md) | ¿Qué tejido queremos delimitar, qué métodos ayudan y dónde falla la evidencia disponible? |
| 3 | [Resultados de desarrollo](../../experiments/ABLATION.md) | ¿Qué ocurrió con HCC_006 y el control HCC_002 al cambiar contexto, ventana y registro? |
| 3a | [Refinamiento del borde POST](post-boundary-refinement.md) | ¿Cómo cambia la máscara con logits, HU y una banda física de 2 mm, y qué sigue sin comprobarse? |
| 3b | [Revisión HCC_006 por cortes](hcc006-slice-review.md) | ¿Dónde persisten prolongaciones ambiguas, discontinuidades, componentes y huecos? |
| 3c | [Investigar los cortes 55–56](post-prompt-conditioning.md) | ¿Influyen el prompt, el seguimiento o la preparación de la imagen? Comparaciones controladas y resultados negativos. |
| 3d | [Refinamiento con otro modelo 3D](post-neural-refinement.md) | ¿Qué cambia al usar nnInteractive sobre la predicción POST completa? Método, ejecución reproducible y comparación por corte. |
| 3e | [Refinamiento axial independiente](post-medsam-slice-refinement.md) | ¿Qué cambia con MedSAM ViT-B y cajas automáticas de los vecinos, sin propagar predicciones entre cortes? |
| 3c | [Superficie 3D de la máscara](lesion-surface.md) | ¿Cómo revisar la geometría guardada sin suavizar ni cambiar la predicción? |
| 4 | [Reproducir los experimentos](../../experiments/README.md) | ¿Cómo ejecutar el CLI y leer su manifiesto, máscaras y diagnósticos? |
| 5 | [Modelos y comparadores](post-tace-models.md) | ¿Qué aportaron nnInteractive y TotalSegmentator y cuáles son sus costes y límites? |
| 6 | [Referencia y validación](post-tace-validation.md) | ¿Cómo conseguir anotaciones independientes y evaluar sin contaminar la prueba? |
| 6a | [Paquete de anotación](annotation-kit.md) y [comandos de preparación y evaluación](../../experiments/README.md#referencia-post-independiente) | ¿Cómo entregar las imágenes a un lector sin mostrar la predicción y comparar después su referencia? |
| 7 | [Revisión experimental](code-review-experiments.md) y [revisión de integración](code-review-runtime.md) | ¿Qué se revisó, qué defectos se corrigieron y qué comprobaciones quedan? |

Para explicar el proyecto oralmente, conviene empezar por el objetivo de la lesión completa, describir el fallo de identidad y terminar con la comparación experimental y la referencia experta que falta. Los símbolos y las fórmulas sirven para entender el procedimiento, no para sustituir esa explicación.

## Aplicación, experimentos y validación

Estado del código al 13 de noviembre de 2026: `2.1.0`, protocolo `liver_context_medsam2_boundary`. **Desplegada y comprobada:** 211/211 pruebas de backend y revisión de ambos visores en HCC_002/HCC_006. La validación anatómica independiente permanece pendiente.

| Estado | Contenido | Documento de referencia |
| --- | --- | --- |
| Implementación actual, 2.1.0 | Registro global → contexto `Liver ∪ Mass` PRE → localización → Efficient MedSAM2 tiny en contexto XY local, todos los cortes z → corte de grafo 3D sobre logits POST, banda de 2 mm. Conserva salida `unreviewed`, conteos previos/finales y controles de rechazo. | [Método automático](../automatic-post-segmentation.md) y [refinamiento](post-boundary-refinement.md). |
| Baseline semiautomático | Semilla POST, región, intervalo HU y conectividad. Tiene matemáticas y supuestos propios. | [Guía](../post-segmentation.md) y [matemáticas del crecimiento](../post-segmentation-math.md). |
| Experimentos separados | Contextos/ventanas, comparadores 3D y sensibilidad de borde con bandas fijas de 1/2/3 mm a partir de una sola inferencia. Las salidas se guardan aparte; no se elige automáticamente una banda ganadora ni un modelo sustituto. | [Runners](../../experiments/README.md), [ablaciones históricas](../../experiments/ABLATION.md) y [comparadores](post-tace-models.md). |
| Arquitectura integrada | Contratos y límites de la ruta adoptada. | [Arquitectura](../../experiments/ARCHITECTURE.md). |
| Validación pendiente | Referencias POST independientes, lectores y prueba en pacientes no utilizados para desarrollar el método. | [Protocolo](post-tace-validation.md). |

Como antecedente de **2.0.0**, la suite integrada con `HCC_PILOT_TEST=1` completó **165/165 pruebas en 139,280 s**, incluido el piloto HCC_002, después de incorporar el flujo de Slicer. Sus comprobaciones de interfaz HCC_002/HCC_006 cubrieron generación, estado sin revisar, visibilidad, persistencia y navegación. La ampliación incluía 31 pruebas sintéticas de preparación, evaluación y geometría de anotación. Estos resultados históricos comprueban contratos de aquella revisión; no validan el nuevo refinamiento ni la exactitud del contorno.

También se instaló y probó **Slicer 5.12.4 en macOS**. Tres ejecuciones gráficas con HCC_006 comprobaron carga nativa, borrador vacío, Paint real, deshacer/rehacer y exportación/reapertura exactas. Las seis comprobaciones de la última ejecución pasaron; los trazos de prueba permanecen separados del paquete del lector y no constituyen referencias expertas. La [guía](annotation-kit.md) incluye el acceso `Open_HCC_in_Slicer.command` y la alternativa multiplataforma; los [comandos de QA](../../experiments/README.md#prueba-funcional-de-slicer) permiten repetir el flujo.

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
