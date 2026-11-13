# Arquitectura y estado del protocolo POST 2

Estado al 12 de noviembre de 2026: implementación `2.0.0`, protocolo `liver_context_local_medsam2`, desplegada localmente. Pasaron 134/134 pruebas integradas y la revisión UI HCC_002 en almacén aislado; la revisión UI HCC_006 también pasó seis comprobaciones. Este documento registra la decisión implementada y sus límites, no una afirmación de exactitud.

La decisión conserva Efficient MedSAM2 tiny y añade refinamiento rígido con contexto hepático y campo XY local. Los ensayos sugieren que estas variables cambian la deriva y el objeto seguido; también muestran sensibilidad material. No se ha escogido una máscara por parecerse al volumen PRE. HCC_002 y HCC_006 son casos de desarrollo sin referencia POST independiente. Los comparadores nnInteractive y TotalSegmentator se evalúan por separado; no son fallback de la aplicación.

## Flujo implementado

1. **Lab/DICOM:** selecciona CT PRE y POST del mismo caso con series/adquisiciones explícitas. Comprueba el segmento `Mass` solicitado y exige exactamente un `Liver` publicado y no vacío. Lee geometría, HU, padding y referencias siguiendo hash → cabecera fresca/etiquetas → decodificación → hash antes de guardar.
2. **Registro global:** estima un Euler3D desde puntos físicos PRE hacia POST, usando imágenes auxiliares de 3 mm. PRE es fixed y POST moving.
3. **Refinamiento de contexto:** vuelve a optimizar ese Euler3D inicial con `Liver ∪ Mass` PRE como máscara de muestreo de MI. Devuelve una transformación PRE → POST completa, no un incremento que deba componerse de nuevo con la global.
4. **Localización:** transforma centro y ocho esquinas de la caja `Mass` PRE a POST. Conserva la caja sin margen para el prompt y otra con margen de búsqueda de 10 mm para coherencia. No remuestrea la máscara PRE como salida.
5. **Adaptador POST:** selecciona un contexto XY físico, infiere con los HU POST y propaga por todos los cortes z. Resta offsets al prompt e inserta las predicciones en los mismos offsets de la retícula nativa POST.
6. **Controles y persistencia:** excluye padding, comprueba cobertura completa de cortes, borde artificial y coherencia con ROI. Lab vuelve a verificar fuentes, shape, dtype, conteos y corte visible. Solo entonces guarda una candidata `unreviewed`; los reportes clínicos siguen separados.

No existe una rama especial por ID de caso. Si falta `Liver`, su etiqueta es ambigua, el refinamiento falla o el modelo se abstiene, se informa el motivo. No se sustituye por otro segmento, una copia PRE, un resultado HU ni un modelo alternativo sin registrar otra ejecución.

## Contrato de registro compartido

```python
refine_pre_to_post(
    pre_hu, post_hu, pre_context_mask, pre_stack, post_stack, initial_transform,
    *, pre_valid=None, post_valid=None,
) -> (transform_pre_lps_to_post_lps, registration_diagnostics)
```

`hcclab.registration` recibe arrays/retículas, no busca etiquetas ni conoce casos concretos. `pre_context_mask` es booleana, tiene la forma PRE exacta y se usa solo para la métrica. `experiments.refine_with_liver` delega en esta función; no mantiene otro optimizador duplicado.

Se rechazan contexto vacío, padding como único contexto, geometría incompatible, transformación inicial distinta de Euler3D (o Composite de un solo Euler), falta de solapamiento, parámetros no finitos y límites de movimiento/tiempo. La configuración usa MI Mattes de 50 bins, muestreo 30 % con semilla 20261112, pirámide 2/1, suavizado 1/0 mm, un hilo de optimización y hasta 150 iteraciones por nivel. Los detalles y ecuaciones están en la [guía de método](../docs/automatic-post-segmentation.md).

La unión evita suponer que `Liver` ya contiene `Mass`. HCC_006 original ilustra esa necesidad: `Mass` 2 y `Liver` 1 tienen intersección cero. La unión sigue siendo contexto PRE; no es una máscara hepática POST ni una referencia tumoral posterior.

## Campo de entrada y salida nativa

```python
segment_post_model(post_hu, spacing_zyx, location, *, checkpoint_path, valid_mask=None)
    -> (mask_native_post_bool, diagnostics)
```

Este adaptador no recibe arrays ni máscara PRE. `hcclab.inference_field.context_bounds` comparte con experimentos la regla física:

`lado_mm = max(160, 2 × max(n_y × dy, n_x × dx))`.

Cada eje se convierte con su propio espaciado. El campo se centra y desplaza dentro del FOV, limitado a sus dimensiones, y debe contener la caja localizada. La caja del prompt usa centros inclusivos de píxel; las cajas de almacenamiento son semiabiertas. Se resta `(x0,y0)` antes de invocar el predictor. La entrada se redimensiona a 512×512 y los logits regresan al tamaño nativo del campo antes de umbralizar e insertarse.

Todos los cortes POST se procesan, sin adoptar el rango z PRE. Los ceros fuera del campo XY significan `not_assessed`: no se demostró que esa región sea fondo. Si la máscara válida toca un límite artificial interno del campo, se rechaza con `incomplete_input_field`; el borde físico adquirido solo genera una advertencia de cobertura. No se recorta una máscara ya calculada a la caja PRE para cumplir un guard.

Tras excluir padding, si más del 50 % del foreground está fuera de la ROI de búsqueda se rechaza con `localization_propagation_mismatch`. Pasar el control no verifica anatomía: el piloto `local240` de HCC_006 muestra por qué. Se mantienen la regla, su origen en desarrollo y sus limitaciones explícitos.

## Procedencia y conservación

- `registration` conserva el refinamiento final y `global_registration`, parámetros iniciales/finales, centro, dirección física, muestras, configuración y motivo de parada.
- `registration.context_segments` y `baseline_source.registration_context_segments` guardan números y etiquetas de `Mass` y `Liver`. Ambos comparten el SOP/hash del SEG original; no se inventan dos archivos fuente.
- La localización conserva centro, caja sin margen, ROI expandida, esquinas transformadas y caras de margen limitadas por el FOV.
- `segmentation.model` registra código, revisión, pesos y configuración verificados; los diagnósticos incluyen caja nativa/local, campo evaluado, retícula, preparación, controles y versiones.
- `method` registra versión `2.0.0`, protocolo `liver_context_local_medsam2` y hashes de registro, orquestador, adaptador, campo de inferencia, assets y carga HU. Las fuentes CT y SEG se verifican nuevamente antes de guardar.

Una abstención nueva permite exportar un diagnóstico sin crear candidata ni volumen sustitutos. No borra una candidata anterior. La máscara histórica fallida HCC_006 permanece con sus metadatos y nota de revisión en almacenamiento local de auditoría separado de la lista activa. Los DICOM originales siguen en solo lectura. Ninguno de estos artefactos médicos debe añadirse a Git ni al contexto de construcción.

## Comprobaciones realizadas y pendientes

Las pruebas dirigidas de registro/experimentos pasaron 21/21 en Docker; cubren ejes físicos rotados, desplazamiento conocido, repetibilidad, padding, contexto inválido, no mutación y reemplazo del SEG durante lectura. La prueba sintética de refinamiento recuperó un desplazamiento LPS conocido de `(4,6,2)` mm con error de 0.3662 mm en el punto objetivo; no mide el registro real de HCC_006.

Las 14 pruebas del adaptador sin Torch/pesos comprobaron contexto anisotrópico 160×80 píxeles, offsets/cajas nativas y locales, padding, todos los cortes z, JSON serializable, rechazo de cada borde artificial y liberación del bloqueo. Son comprobaciones de implementación, no una suite completa de aceptación ni evidencia clínica.

Una reproducción del CLI desde CT/SEG originales para HCC_006 `local160liver` terminó en 50.83 s de inferencia, con 11120 vóxeles, cortes 49–64 y 0 % fuera de ROI. El runner guarda predicciones experimentales separadas y omite el rechazo de ROI para estudiar fallos; ese resultado no sustituye la comprobación del endpoint integrado. El [registro de ablaciones](ABLATION.md) conserva también las predicciones fallidas y la sensibilidad entre variantes.

La suite completa `HCC_PILOT_TEST=1` terminó con 134/134 pruebas aprobadas en 135.691 s. La revisión UI HCC_002 pasó ocho grupos de comprobaciones con ambas adquisiciones 1, Mass 2 y Liver 1: endpoint 49.028 s, 308628 vóxeles candidatos y navegación durante inferencia de 196 ms PRE/133 ms POST. Las capturas de escritorio/móvil y el informe se conservaron localmente. El contenedor QA8771 se retiró y la app8770 permaneció saludable. La revisión UI HCC_006 también pasó sus seis comprobaciones. Las etiquetas clínicas, fase, correspondencia y exactitud continúan sin verificarse; una referencia POST independiente y revisión experta siguen pendientes.

## Comparadores y reproducción

La revisión UI HCC_006 terminó con seis comprobaciones aprobadas y candidata `unreviewed` de 11172 vóxeles (PRE adquisición 3, POST adquisición 1, Mass 2 y Liver 1). Tiempos: 51.651 s de UI, 50.2888 s de operación backend y 48.474 s de inferencia; navegación PRE/POST 58/67 ms. La auditoría posterior no modificó la máscara y confirmó retícula/fuentes, padding y bordes. La comparación anatómica entre modelos se autorizó solo por igualdad exacta de hashes CT ordenados y geometría; no constituye GT ni validez clínica. Los detalles están en [ABLATION.md](ABLATION.md).

El [runner reproducible](README.md) recibe casos/adquisiciones/segmentos explícitos y vuelve a calcular el registro desde las fuentes. El [índice de investigación](../docs/research/README.md), las [matemáticas](../docs/research/post-mathematics.md) y la [evidencia de modelos](../docs/research/post-tace-models.md) documentan alternativas y requisitos de evaluación.

Un modelo autónomo de lesiones hepáticas, si se adopta posteriormente, debe segmentar POST sin máscara PRE y separar esa salida de la identificación longitudinal del objetivo. Si hay varios objetos, no deben sumarse sus volúmenes ni recortarse a la caja PRE para fabricar correspondencia. Ausencia, ambigüedad y varias componentes requieren estados explícitos y revisión. Esta posibilidad no forma parte del protocolo 2 implementado aquí.
