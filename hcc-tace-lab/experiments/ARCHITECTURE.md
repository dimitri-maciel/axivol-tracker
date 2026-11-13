# Arquitectura y estado del protocolo POST 2.1

Estado del código al 13 de noviembre de 2026: implementación `2.1.0`, protocolo `liver_context_medsam2_boundary`. **Desplegada y comprobada:** 189/189 pruebas de backend, generación real de HCC_006 y siete grupos de comprobaciones de navegador en HCC_002/HCC_006. Se mantiene pendiente la validación anatómica independiente. Los ensayos de 2.0.0 se conservan abajo como antecedentes; no son una afirmación de exactitud ni de aceptación de esta revisión.

La revisión conserva registro hepático y contexto XY local, y añade un [refinamiento 3D del borde](../docs/research/post-boundary-refinement.md) guiado por logits y HU POST en una banda fija de 2 mm. No se escoge una máscara por parecerse al volumen PRE ni se corrige una localización equivocada por el mero hecho de reducir energía. HCC_002/HCC_006 siguen siendo casos de desarrollo sin referencia POST independiente. nnInteractive y TotalSegmentator permanecen como comparadores separados.

## Flujo implementado

1. **Lab/DICOM:** selecciona CT PRE y POST del mismo caso con series/adquisiciones explícitas. Comprueba el segmento `Mass` solicitado y exige exactamente un `Liver` publicado y no vacío. Lee geometría, HU, padding y referencias siguiendo hash → cabecera fresca/etiquetas → decodificación → hash antes de guardar.
2. **Registro global:** estima un Euler3D desde puntos físicos PRE hacia POST, usando imágenes auxiliares de 3 mm. PRE es fixed y POST moving.
3. **Refinamiento de contexto:** vuelve a optimizar ese Euler3D inicial con `Liver ∪ Mass` PRE como máscara de muestreo de MI. Devuelve una transformación PRE → POST completa, no un incremento que deba componerse de nuevo con la global.
4. **Localización:** transforma centro y ocho esquinas de la caja `Mass` PRE a POST. Conserva la caja sin margen para el prompt y otra con margen de búsqueda de 10 mm para coherencia. No remuestrea la máscara PRE como salida.
5. **Adaptador POST:** infiere en contexto XY físico y propaga por todos los cortes z. Conserva logits nativos de campo; en el corte ancla retiene los logits originales condicionados por la caja, evitando reemplazarlos por puntuaciones fuertes fabricadas al reinyectar la máscara binaria.
6. **Refinamiento POST:** minimiza una energía de logits y diferencias HU entre seis vecinos dentro de una banda de 2 mm. Mantiene el resto de etiquetas y padding; inserta la máscara final en los offsets nativos. Menor energía no significa mayor exactitud anatómica.
7. **Controles y persistencia:** comprueba cobertura, bordes artificiales y coherencia con ROI antes y después del ajuste. Una banda editable que toca el borde artificial también se rechaza. Lab vuelve a verificar fuentes, shape, dtype, conteos y corte visible antes de guardar una candidata `unreviewed`; los reportes clínicos siguen separados.

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
segment_post_model(post_hu, spacing_zyx, location, *, checkpoint_path, valid_mask=None,
                   refine_boundary=True, intermediates=None)
    -> (mask_native_post_bool, diagnostics)
```

Este adaptador no recibe arrays ni máscara PRE. `hcclab.inference_field.context_bounds` comparte con experimentos la regla física:

`lado_mm = max(160, 2 × max(n_y × dy, n_x × dx))`.

Cada eje se convierte con su propio espaciado. El campo se centra y desplaza dentro del FOV, limitado a sus dimensiones, y debe contener la caja localizada. La caja del prompt usa centros inclusivos de píxel; las cajas de almacenamiento son semiabiertas. Se resta `(x0,y0)` antes de invocar el predictor. La entrada se redimensiona a 512×512 y los logits regresan al tamaño nativo del campo antes de umbralizar e insertarse.

Todos los cortes POST se procesan, sin adoptar el rango z PRE. Los ceros fuera del campo XY significan `not_assessed`: no se demostró que esa región sea fondo. Si la máscara válida toca un límite artificial interno del campo, se rechaza con `incomplete_input_field`; si lo toca su banda editable, con `incomplete_boundary_context`. El borde físico adquirido se registra aparte. No se recorta la salida a la caja PRE para cumplir un guard. `refine_boundary=False` es un comparador explícito; no es una salida de respaldo si falla el refinamiento.

Tras excluir padding, si más del 50 % del foreground está fuera de la ROI de búsqueda se rechaza con `localization_propagation_mismatch`. Pasar el control no verifica anatomía: el piloto `local240` de HCC_006 muestra por qué. Se mantienen la regla, su origen en desarrollo y sus limitaciones explícitos.

## Procedencia y conservación

- `registration` conserva el refinamiento final y `global_registration`, parámetros iniciales/finales, centro, dirección física, muestras, configuración y motivo de parada.
- `registration.context_segments` y `baseline_source.registration_context_segments` guardan números y etiquetas de `Mass` y `Liver`. Ambos comparten el SOP/hash del SEG original; no se inventan dos archivos fuente.
- La localización conserva centro, caja sin margen, ROI expandida, esquinas transformadas y caras de margen limitadas por el FOV.
- `segmentation.model` registra pesos/configuración; `raw_prediction` conserva conteo y hashes de máscara/logits previos, mientras `boundary_refinement` registra banda, añadidos/retirados, energía y distancias de cambio. El conteo final sigue en `segmentation.voxels`. Los logits no son confianza calibrada.
- `method` registra versión `2.1.0`, protocolo `liver_context_medsam2_boundary` y hashes de registro, orquestador, adaptador, campo, assets, carga HU y ahora `post_boundary.py` y `refinement.py`. Las fuentes CT/SEG se verifican nuevamente antes de guardar; las candidatas antiguas conservan versión y bytes.

Una abstención nueva permite exportar un diagnóstico sin crear candidata ni volumen sustitutos. No borra una candidata anterior. La máscara histórica fallida HCC_006 permanece con sus metadatos y nota de revisión en almacenamiento local de auditoría separado de la lista activa. Los DICOM originales siguen en solo lectura. Ninguno de estos artefactos médicos debe añadirse a Git ni al contexto de construcción.

## Comprobaciones históricas de 2.0.0

Las pruebas dirigidas de registro/experimentos pasaron 21/21 en Docker; cubren ejes físicos rotados, desplazamiento conocido, repetibilidad, padding, contexto inválido, no mutación y reemplazo del SEG durante lectura. La prueba sintética de refinamiento recuperó un desplazamiento LPS conocido de `(4,6,2)` mm con error de 0.3662 mm en el punto objetivo; no mide el registro real de HCC_006.

Las 14 pruebas del adaptador sin Torch/pesos comprobaron contexto anisotrópico 160×80 píxeles, offsets/cajas nativas y locales, padding, todos los cortes z, JSON serializable, rechazo de cada borde artificial y liberación del bloqueo. Son comprobaciones de implementación, no una suite completa de aceptación ni evidencia clínica.

Una reproducción del CLI desde CT/SEG originales para HCC_006 `local160liver` terminó en 50.83 s de inferencia, con 11120 vóxeles, cortes 49–64 y 0 % fuera de ROI. El runner guarda predicciones experimentales separadas y omite el rechazo de ROI para estudiar fallos; ese resultado no sustituye la comprobación del endpoint integrado. El [registro de ablaciones](ABLATION.md) conserva también las predicciones fallidas y la sensibilidad entre variantes.

La suite completa `HCC_PILOT_TEST=1` terminó con 134/134 pruebas aprobadas en 135.691 s. La revisión UI HCC_002 pasó ocho grupos de comprobaciones con ambas adquisiciones 1, Mass 2 y Liver 1: endpoint 49.028 s, 308628 vóxeles candidatos y navegación durante inferencia de 196 ms PRE/133 ms POST. Las capturas de escritorio/móvil y el informe se conservaron localmente. El contenedor QA8771 se retiró y la app8770 permaneció saludable. La revisión UI HCC_006 también pasó sus seis comprobaciones. Las etiquetas clínicas, fase, correspondencia y exactitud continúan sin verificarse; una referencia POST independiente y revisión experta siguen pendientes.

## Comparadores y reproducción

La revisión UI **2.0.0** de HCC_006 terminó con seis comprobaciones y candidata `unreviewed` de 11172 vóxeles (PRE adquisición 3, POST adquisición 1, Mass 2 y Liver 1). La auditoría no modificó esa máscara; su comparación anatómica entre modelos exigió hashes CT y retícula idénticos y no constituye GT. Los tiempos y detalles históricos están en [ABLATION.md](ABLATION.md).

Los [runners reproducibles](README.md) recalculan el registro desde fuentes explícitas. `post_tracking` conserva las ablaciones históricas; `post_boundary` guarda raw, logits de campo y refinamientos independientes de 1/2/3 mm, con 2 mm fijada como principal. No selecciona una banda por sus resultados ni escribe candidatas. El [índice de investigación](../docs/research/README.md) enlaza matemáticas, evidencia y evaluación pendiente.

Un modelo autónomo de lesiones hepáticas, si se adopta posteriormente, debe separar segmentación POST e identificación longitudinal. No deben sumarse objetos ni recortarse a la caja PRE para fabricar correspondencia. Esta posibilidad no forma parte de 2.1.0.
