# Revisión de integración del registro hepático y contexto POST

Fecha: 12 de noviembre de 2026. Base de comparación: `42f8234fed9f5a46070b1a102d12afc20a36e27f` (`Clarify POST mask visibility and reject gross propagation drift`). La revisión comenzó durante la integración y se actualizó después del despliegue local y las comprobaciones técnicas del protocolo 2. **Estas comprobaciones no acreditan exactitud anatómica ni validación clínica.**

## Resultado

No quedaron hallazgos de código accionables dentro del alcance revisado. Se identificó y cerró una carencia de cobertura: faltaban pruebas de la selección de `Liver` y de la procedencia del contexto en la operación de aplicación. Se añadieron cinco pruebas sintéticas, todas satisfactorias. En la revisión paralela del adaptador se corrigió un problema de serialización: `field_shape` contenía enteros NumPy que JSON no admite; ahora se convierte a `tuple(int(value) for value in high - low)` antes de construir los diagnósticos.

El flujo conserva la separación entre **localización desde PRE** y **segmentación de los píxeles POST**. Si el refinamiento falla, no vuelve silenciosamente al registro global ni guarda una máscara alternativa. Las pruebas sintéticas de esta revisión y las comprobaciones integradas posteriores se distinguen abajo; estas últimas incluyen inferencia y controles de interfaz en los casos de desarrollo HCC_002 y HCC_006.

## Alcance y procedimiento

Se aplicaron las guías `differential-review` y `karpathy-guidelines`: comparación con una base concreta, inspección de historia y llamadas, revisión de controles de entrada, fallos y persistencia, y pruebas centradas en las invariantes modificadas.

Archivos principales:

- [`lab.py`](../../hcclab/lab.py): `create_automatic_post_candidate`, extracción de segmentos originales y metadatos.
- [`post_automatic.py`](../../hcclab/post_automatic.py): registro global, refinamiento con contexto, localización y llamada al modelo.
- [`registration.py`](../../hcclab/registration.py): copia de transformación rígida, máscara auxiliar y refinamiento físico.

Se leyeron las dependencias inmediatas de procedencia/geometría y guardado: `dicom.py`, `volume.py`, `candidates.py` y el límite HTTP de `server.py`. El revisor de integración comprobó el contrato del modelo con la aplicación. El revisor de algoritmo examinó en paralelo [`post_model.py`](../../hcclab/post_model.py) y [`inference_field.py`](../../hcclab/inference_field.py): recorte XY, propagación, retorno nativo, límites y serialización. Sus resultados se incorporan como revisión delegada; no se presentan como una segunda auditoría independiente de esos archivos.

La inspección `git blame` sitúa la validación anterior de `Mass` en el cambio `2c2a8424`. Esa validación se conserva y se mueve después de releer/verificar la cabecera SEG, antes de extraer píxeles. No se eliminó un rechazo ni se amplió el acceso HTTP.

## Impacto y controles comprobados

| Cambio | Alcance directo de llamadas de producción | Comprobación |
| --- | --- | --- |
| Operación de aplicación | Una ruta HTTP: `/api/automatic-post-candidates` | La misma selección de caso, series, adquisiciones y segmento sigue siendo obligatoria. |
| Orquestador automático | Una llamada desde `Lab` | El refinamiento recibe PRE/POST y contexto; el modelo recibe únicamente POST, espaciado y localización. |
| `refine_pre_to_post` | Dos llamadas: orquestador y adaptador experimental | El optimizador compartido evita divergencias entre ambos caminos. |
| `register_pre_to_post` | Dos llamadas: orquestador y ejecutor experimental | El registro global queda registrado como antecedente; no se compone dos veces con el refinado. |

**Fuentes.** Se comprueba `Mass` sobre la cabecera recién verificada y se exige una sola etiqueta `Liver`. Ambas máscaras proceden del mismo SEG original y de sus referencias DICOM a la adquisición PRE. Una máscara de órgano vacía, ausente o ambigua produce rechazo. No se utiliza una edición PRE guardada como fuente. El SHA-256 del SEG cubre ambos segmentos; sus números y etiquetas quedan explícitos en `context_segments` y en la procedencia de la candidata.

**Geometría.** La unión `Liver | Mass` es un contexto de muestreo de la métrica PRE. El auxiliar se remuestrea con vecino más cercano a la rejilla de registro, sin transformarlo en una etiqueta POST. Se comprueban booleanos, forma nativa, datos finitos, soporte válido y un mínimo de muestras. El transformado inicial debe ser Euler3D o un compuesto de un solo Euler3D; se conserva y se valida antes de optimizar. La transformación refinada mantiene la dirección PRE LPS → POST LPS y sustituye a la estimación inicial para localizar centro/caja.

**Persistencia.** El resultado debe ser booleano, no vacío, de forma POST nativa y sin píxeles inválidos. El conteo y el corte de visualización deben concordar con la máscara. El volumen usa el espaciado POST, no el tamaño del segmento PRE. Los archivos de fuente se verifican antes y después; las candidatas se guardan con un ID nuevo y escritura temporal seguida de renombrado. Los originales y versiones previas permanecen intactos.

**Metadatos.** La versión del método pasa a `2.0.0`, con protocolo `liver_context_local_medsam2`. Se conserva el campo XY realmente inferido, si se redimensionó y la advertencia `outside_inference_field = not_assessed`. El estado continúa como `unreviewed`; registro, fase y correspondencia anatómica siguen sin verificar. El informe clínico no convierte esta candidata en medida POST validada ni en cambio porcentual de tratamiento.

**Fallos.** Un error de refinamiento conserva el registro global y marca segmentación no iniciada. Una abstención del modelo conserva el registro y la procedencia, sin máscara ni volumen sustitutos. El bloqueo se libera en `finally`; las pruebas también comprueban que los rechazos de contexto no dejan almacenamiento nuevo. Las cotas temporales del optimizador se comprueban entre iteraciones: no equivalen a interrumpir inmediatamente una llamada nativa bloqueada.

## Pruebas sintéticas de la revisión

Se usó la imagen de aplicación mediante `compose.yaml`, montando el código y las pruebas actuales de solo lectura; cada prueba escribió únicamente sus archivos sintéticos temporales. No se activaron pruebas piloto, descargaron pesos ni procesaron imágenes de pacientes.

| Grupo | Resultado observado | Qué comprueba |
| --- | --- | --- |
| `test_registration` + `test_post_automatic` | **15/15**, 0,194 s | Ejes físicos rotados, espaciado anisótropo, traslación sintética, padding, entradas inválidas, conservación de arrays, paso del transformado refinado y ausencia de fallback. |
| [`test_automatic_context_sources.py`](../../tests/test_automatic_context_sources.py) | **5/5**, 0,033 s | `Liver` ausente/duplicado/vacío; unión y procedencia en éxito simulado; volumen POST nativo; abstención sin guardado y desbloqueo. |
| [`test_post_model.py`](../../tests/test_post_model.py), revisión delegada | **14/14** | Offsets anisotrópicos, caja local/nativa, todos los cortes z, cuatro bordes XY artificiales, padding, desbloqueo y JSON serializable; sin pesos ni inferencia neuronal. |

Los dobles de prueba del segundo grupo sustituyen la lectura DICOM y la inferencia: verifican la integración de la operación, no la exactitud de los decodificadores o del modelo. La prueba física de registro es sintética y tampoco mide correspondencia anatómica en HCC_006.

## Verificación integrada posterior

El equipo ejecutó en el entorno local desplegado:

```sh
docker compose exec -T -e HCC_PILOT_TEST=1 app python -m unittest discover -s tests -v
```

Resultado comunicado y registrado al cierre: **134/134 pruebas satisfactorias, 135,691 s**, incluido el piloto HCC_002. Esta ejecución completa contiene pruebas de los grupos anteriores; sus recuentos no se suman como si fueran observaciones independientes.

También pasaron los ocho grupos de comprobaciones de interfaz HCC_002 y la prueba real HCC_006: selección de fuentes, generación, estado `unreviewed`, overlay visible, ocultar/mostrar, restauración tras recarga y navegación durante la operación. HCC_006 produjo la candidata `379a27b9134e4e6b9f6b58d40ccce09e`, con 11 172 vóxeles y campo XY `[[236,95],[441,300]]`; el corte de máxima área fue índice interno 58, mostrado como 59. Es un resultado de ejecución del protocolo 2, no una medida tumoral validada ni el piloto histórico de 11 485 vóxeles.

Las tres comprobaciones adicionales de estados de máscara pasaron sin escribir nuevas candidatas. La evidencia visual de HCC_006 permanece local. Las verificaciones de interfaz fueron ejecutadas por otros miembros del equipo y se incorporan a este cierre; no fueron repetidas por el revisor de integración.

## Límites y trazabilidad de esta revisión

No se ejecutaron analizadores automáticos de seguridad ni pruebas de carga, ni se realizó una evaluación experta de contornos. No se auditaron bibliotecas externas completas, pesos ni todas las rutas de la aplicación. Las ejecuciones reales y de interfaz anteriores verifican funcionamiento; no miden sensibilidad, Dice contra una referencia o respuesta al tratamiento. La cobertura indicada es por casos ejecutados; no es un porcentaje de cobertura de líneas.

Los SHA-256 siguientes fijan los archivos principales al cierre de la revisión. Un cambio posterior requiere comprobar al menos su diferencia y las pruebas afectadas.

| Archivo | SHA-256 |
| --- | --- |
| `hcclab/lab.py` | `c4f632c4241039b1377ae5eed984378ec5c50be0446a217d7e433dee7d455857` |
| `hcclab/post_automatic.py` | `92d86bf7ca6b1669a934e406ac8ad2ab8f34ea829e7e38aa34f44533da66224d` |
| `hcclab/registration.py` | `adbd2df8c22b0023ed6ed9baae9608be62302f3f266e19570baf5571df4b44d2` |
| `hcclab/post_model.py`, revisión delegada | `ddabe87f90faa9592358a6a62ba1f27f7301d58d068733524e3da9c4e5f8ddfe` |
| `hcclab/inference_field.py`, revisión delegada | `3074348c277b3feb9cb9e8aae76012a71e43288c0c2af3cb4684514b687b78f6` |
| `tests/test_automatic_context_sources.py` | `b615d446ffb14df9211c7d775a1eb3a1ed05845621b9d91ee1098dcdc0df3598` |

La precisión y utilidad de la propuesta deben evaluarse con el [protocolo independiente](post-tace-validation.md). Que la integración conserve geometría y procedencia no demuestra que el tejido seleccionado sea la lesión correcta.
