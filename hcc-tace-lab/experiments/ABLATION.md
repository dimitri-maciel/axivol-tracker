# Ablación de seguimiento POST: HCC_006 y control HCC_002

**Ampliación, 13 de noviembre:** el [experimento de refinamiento de borde](../docs/research/post-boundary-refinement.md) conserva logits POST y compara bandas físicas de 1, 2 y 3 mm desde una misma inferencia por caso. La banda principal de 2 mm modifica 406 vóxeles en HCC_006 y 8050 en HCC_002. Son cambios respecto a cada predicción inicial, no errores frente a una referencia. Los resultados siguientes pertenecen a las variantes históricas de localización y contexto; no deben mezclarse con esas nuevas pasadas.

Fecha de ejecución: 12 de noviembre de 2026. Objetivo: investigar una deriva observada del seguimiento automático, no demostrar exactitud de segmentación. **No hay referencia POST independiente.** HCC_006 y HCC_002 son casos de desarrollo; los parámetros se exploraron tras observar un fallo y no constituyen evaluación externa.

Fuentes: colección pública HCC-TACE-Seg; HCC_006 PRE adquisición 3, POST adquisición 1, `Mass` segmento 2; HCC_002 PRE adquisición 1, POST adquisición 1, `Mass` segmento 2. El refinamiento hepático de HCC_006 usa además `Liver` segmento 1 únicamente para registro. El manifiesto técnico permite recuperar las series exactas desde CT/SEG originales.

## Resultado observado

| Caso y variante | Vóxeles predichos | Fuera de ROI de búsqueda | Cortes con foreground, inclusivos desde 0 | Tiempo de inferencia |
|---|---:|---:|---|---:|
| HCC_006 `full` | 184806 | 90.884 % | 4–59 | 51.64 s |
| HCC_006 `local160` | 17167 | 0 % | 46–61 | 52.49 s |
| HCC_006 `local240` | 24492 | 26.723 % | 43–63 | 55.22 s |
| HCC_006 `local160wide` | 177974 | 89.795 % | 3–61 | 55.04 s |
| HCC_006 `local160liver` | 11485 | 0 % | 49–64 | 55.67 s |
| HCC_002 `local160` | 360865 | 0 % | 44–87 | 50.69 s |

El control HCC_002 `full` ya guardado contenía 444793 vóxeles; se utilizó para comparar predicciones sin ejecutarlo de nuevo en esta ronda. El pico de proceso instrumentado de la primera serie estuvo alrededor de 979–1006 MiB. El tiempo/pico son mediciones de ingeniería en el entorno local, no rendimiento garantizado.

Ninguna de las seis predicciones tocó el borde XY de su campo de inferencia. Todos los cortes POST se procesaron. Eso descarta un recorte directo del foreground como explicación de los cambios, pero no demuestra que el contexto excluido no influya en la red.

## Qué falló y qué cambió

1. **Campo completo:** el modelo pasó del corte inicial a una estructura inferior de apariencia renal. La componente dominante era 184795 de 184806 vóxeles y seguía conectada al prompt. El prompt inicial reproducido tenía 1166 píxeles y compartía 1140 con esa componente. Conectividad 6 y 26 dieron el mismo resultado: conservar la componente del prompt no soluciona esta deriva.
2. **Contexto local de 160 mm:** desapareció esa extensión inferior en la inspección del piloto. El campo XY `[221,81]–[426,286]` contenía íntegramente la caja XY de la componente renal previa `[242,106]–[351,213]`; no se la ocultó fuera del campo. Sin embargo, la máscara excluyó casi todos los vóxeles de alta intensidad junto al objetivo. Esto impide presentarla como una delimitación demostrada de `Mass` global.
3. **Sensibilidad a 240 mm:** reapareció una extensión de apariencia renal en el corte 43. Solo 26.7 % del foreground quedó fuera de ROI: el guard de mayoría exterior la aceptaría. Es evidencia concreta de que pasar ese guard no valida la anatomía.
4. **Ventana 200/800:** volvió una extensa deriva inferior. Una ventana más amplia no corrigió por sí sola la exclusión de alta intensidad.
5. **Refinamiento hepático + contexto local:** la predicción se desplazó hacia el foco hiperdenso y puede omitir tejido periférico menos intenso. Cambió el objeto preferido, no solo su borde. Tampoco se puede asumir que esta versión sea la referencia correcta.
6. **Control HCC_002:** el contexto local produjo 18.9 % menos foreground que el resultado completo anterior. Ese cambio demuestra sensibilidad al contexto; no prueba que un volumen menor sea más preciso.

Los scores de objeto de `full` permanecieron positivos durante la deriva. Por ejemplo, bajaron aproximadamente de 3.89 en corte 55 a 0.17–0.54 en parte del tramo 44–35 y luego aumentaron. No hubo una desaparición que permitiera corregir el fallo simplemente deteniéndose ante un score negativo. Estos logits no están calibrados como probabilidad anatómica y no se ajustó un umbral nuevo para hacer pasar el caso.

## Intensidades en una caja común

Se contaron intensidades en la caja localizada global original de HCC_006, igual para todas las variantes: 4414 vóxeles mayores de 240 HU y 2896 mayores de 600 HU. No se los identifica automáticamente como Lipiodol, tumor o referencia anatómica.

| Variante HCC_006 | >240 HU incluidos / 4414 | >600 HU incluidos / 2896 |
|---|---:|---:|
| `full` | 378 | 29 |
| `local160` | 8 | 0 |
| `local240` | 29 | 0 |
| `local160wide` | 191 | 0 |
| `local160liver` | 4150 | 2818 |

La diferencia es demasiado grande para atribuirla solo a suavizado del contorno. La caja/registro/prompt altera qué región intenta seguir el modelo. El refinamiento hepático también cambió el corte inicial de 56 a 59 y el centro XY; este ensayo no separa causalmente esos cambios y necesita una ablación adicional si se quiere atribuir el efecto a uno de ellos.

## Consistencia entre predicciones, no Dice contra GT

| Comparación | Dice entre predicciones |
|---|---:|
| HCC_006 `full` vs `local160` | 0.1136 |
| HCC_006 `local160` vs `local240` | 0.5963 |
| HCC_006 `local160` vs `local160wide` | 0.1379 |
| HCC_006 `local160` vs `local160liver` | 0.0859 |
| HCC_002 `local160` vs `full` previo | 0.8619 |

La tabla completa y los hashes de los artefactos originales están en [development_results.json](development_results.json). Ninguna de estas comparaciones usa una máscara PRE como referencia POST ni presenta el resultado automático como verdad.

## Decisión de desarrollo

El refinamiento hepático y contexto local se incorporaron al protocolo `liver_context_local_medsam2`, versión 2.0.0, desplegado localmente al 12 de noviembre de 2026. Pasaron 134/134 pruebas integradas y la revisión UI real HCC_002; la revisión UI HCC_006 también pasó seis comprobaciones. Esta decisión no los convierte en una mejora anatómica demostrada. La sensibilidad a ventana y localización es material. No se ha escogido una máscara por parecerse al volumen PRE, ni se ha unido/recortado la salida para forzar un objetivo esperado. La [arquitectura y estado](ARCHITECTURE.md) documentan los contratos y controles adicionales del código integrado.

Los siguientes experimentos deben separar posición del prompt, caja y contexto; estudiar consistencia entre escalas; y comparar contra referencias POST independientes con un objetivo tumoral definido y revisión experta. Una restricción de identidad o de campo de inferencia debe documentar su hipótesis y las regiones no evaluadas. La candidata fallida HCC_006 sigue separada de la lista activa de la app.

## Reproducción y límites del registro histórico

Los pilotos se ejecutaron secuencialmente con Efficient MedSAM2 tiny, commit `332f30d420f1d1b08e2a79b3ae6a602458808383`, pesos SHA-256 `78cd67f41c1c2953026bd6e70a7648998079d08cb6ab0f75bbe289039695c103`, Torch 2.5.1, SimpleITK 2.5.7, NumPy 2.5.4 y SciPy 1.18.1. Los scripts temporales reutilizaban el registro previo únicamente para fijar esa variable en la comparación.

El runner versionable de [README.md](README.md) reemplaza esa dependencia por registro recalculado desde CT/SEG originales y escribe procedencia completa por ejecución. Los agregados anteriores son resultados históricos de los pilotos, no una afirmación de que ya se repitió todo el lote con el CLI extraído. Las diferencias de plataforma o ejecución multihilo pueden producir diferencias numéricas; deben compararse los registros y manifiestos, no prometer igualdad bit a bit.

### Reproducción desde las fuentes originales

Se ejecutó el CLI nuevo para HCC_006 `local160liver` con PRE adquisición 3, POST adquisición 1, `Mass` 2 y `Liver` 1. La ejecución `run-20261113T014341-0c046bff` terminó: 11120 vóxeles, una componente 6-conexa, cortes 49–64, máxima área en corte 58, 0 % fuera de ROI y 50.83 s de inferencia. El hash del NPZ es `e35324592513a25eb1c19a375b44a7ae56675e427aaa40238adc3555fd3f147d`. No leyó candidatas activas ni cuarentena y no guardó una candidata en la app.

El centro POST global recalculado fue `[55.7090,323.4164,183.9416]` y el refinado `[59.1471,337.5636,197.9302]`, en índices continuos `[z,y,x]`. El piloto histórico usó `[59.0094,337.5108,196.9395]` y produjo 11485 vóxeles. La diferencia de 365 vóxeles muestra que repetir con registro recalculado no equivale a fijar exactamente el mismo prompt; no se atribuye causalmente a un único paso ni se interpreta como error anatómico.

La inspección de la fuente original confirmó que `Mass` PRE contiene 8404 vóxeles (mediana 91 HU, máximo 219 HU; ninguno >240 HU ni >600 HU). Su intersección con `Liver` es cero. Por tanto, la alta intensidad observada dentro de las cajas POST no estaba presente a esos umbrales en la máscara PRE publicada. Esto describe intensidades de regiones distintas: no identifica material, no demuestra respuesta y no sustituye una referencia POST. El montaje y las máscaras permanecen solo en los artefactos locales ignorados por Git.

### Comprobación UI integrada de HCC_002

El protocolo 2 integrado se ejecutó una vez desde la interfaz sobre HCC_002, PRE y POST adquisición 1, `Mass` 2 y `Liver` 1, usando un almacén QA separado. Produjo 308628 vóxeles, máxima área en corte 72 desde cero, 665 vóxeles fuera de la ROI (0.2155 %) y ningún contacto con borde artificial. El endpoint tardó 49.028 s; los cambios de corte durante inferencia tardaron 196 ms PRE y 133 ms POST. Pasaron los ocho grupos del runner UI: navegación, restauración, borradores, respuestas tardías, separación de mediciones y presentación responsive. Estos datos pertenecen al protocolo con refinamiento hepático, no sustituyen la fila histórica `local160` con registro global de la tabla inicial.

La candidata mantiene estado `unreviewed`. El test no mide exactitud contra GT ni convierte sus vóxeles en respuesta al tratamiento. Capturas e informe quedan locales en `output/post-improvement/ui002`; el contenedor QA fue retirado sin tocar la app o sus anotaciones persistentes.

### Comprobación UI y auditoría integrada de HCC_006

El mismo protocolo 2 pasó seis comprobaciones de navegador sobre HCC_006, PRE adquisición 3 y POST adquisición 1, `Mass` 2 y `Liver` 1. Generó una candidata de 11172 vóxeles sin alterar las anteriores. La UI tardó 51.651 s; el backend registró 50.2888 s de operación y 48.474 s de inferencia. La navegación PRE/POST durante generación tardó 58/67 ms. Se comprobaron identidades, superposición visible, ocultar/mostrar y restauración al recargar.

La auditoría de solo lectura confirmó caja `[49,310,173]–[65,359,221)` y ausencia de padding, contacto con borde artificial o vóxeles fuera de ROI. Hay tres componentes 6-conexas: 11170, 1 y 1 vóxeles; con 26 vecinos son 11171 y 1. No se cambió la máscara. Se verificaron de nuevo hashes CT/SEG y del NPZ antes y después de leerlo.

La comparación con TotalSegmentator se hizo tras igualdad exacta de hashes CT ordenados, referencias SOP y retícula: 0 intersección con ambas predicciones renales, y 2152 vóxeles (19.26 %) con la predicción de hígado. Ese desacuerdo entre modelos no establece verdad anatómica; el hígado automático puede excluir la región tratada. Tampoco permite afirmar que se cubra toda `Mass` POST. El montaje multiplanar, informe y captura UI se conservaron en artefactos locales, sin inferir otra máscara.

El flujo de caja a máscara y propagación se contrastó con el [script oficial fijado](https://github.com/bowang-lab/MedSAM2/blob/332f30d420f1d1b08e2a79b3ae6a602458808383/eff_medsam2_infer_CT_lesion_npz_recist.py). Ese script usa RECIST POST y extensión z indicada. La ausencia de esa indicación en Axivol es una diferencia metodológica explícita.
