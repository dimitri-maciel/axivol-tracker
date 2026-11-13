# Interpretación de los dossiers

Estos archivos fijan artefactos y distinguen hechos comprobados de información pendiente. Los `.gate.json` se generan con el verificador local de la skill `model-selection`; un código de salida 1 indica hallazgos mayores, no un fallo de ejecución del verificador.

- La ausencia de un inventario de entrenamiento no se rellena con `[]`: eso habría afirmado que el modelo no se entrenó con ningún conjunto.
- nnInteractive puede ejecutarse como piloto local, pero no se presenta HCC-TACE-Seg como validación externa independiente de su entrenamiento.
- LesionLocator ya usó HCC-TACE-Seg como benchmark publicado. El aviso de conflicto no dice que entrenó con esas imágenes; señala que repetir ese benchmark no es una nueva demostración independiente.
- TotalSegmentator anatómico es un comparador de órganos. El aviso de tarea distinta es intencional: sus máscaras no son referencias tumorales.
- SegVol conserva `LICENCE_UNSTATED` para los pesos; la licencia MIT del código no basta para resolverla.
- `VALIDATION_UNREPORTED` significa que este dossier no incorpora una validación verificable del artefacto exacto; no afirma que el artículo carezca de experimentos.
- Verificar CPU significa haber completado la ruta concreta anotada. No implica rendimiento clínico, independencia de datos ni compatibilidad con cualquier equipo.

Los modelos cuyo entrenamiento no pudo reconstruirse por completo pueden ayudar a desarrollar herramientas con salidas explícitamente sin revisar. Los hallazgos limitan las afirmaciones científicas y señalan qué información falta antes de evaluar precisión o desplegar clínicamente.
