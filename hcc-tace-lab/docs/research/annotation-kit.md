# Guía para preparar una referencia POST independiente

**Estado:** material preparado para una futura anotación. Actualmente no hay un especialista en imagen hepática asignado. Preparar el paquete, practicar en Slicer o completar un formulario no valida el algoritmo. Una práctica propia debe identificarse como tal y no presentarse como referencia experta.

## 1. Qué recibe el lector

Entregar únicamente la carpeta `reader/`:

| Archivo | Uso |
| --- | --- |
| `POST_CT.nrrd` | Imagen que se delimita, en su rejilla nativa. |
| `PRE_CT.nrrd`, `PRE_Mass.nrrd` | Imagen y máscara PRE publicada, para identificar la lesión correspondiente. |
| `case.json`, `reader_form.json` | Identificación de caso, adquisición y paquete; declaración del lector. |
| `README.md`, `open_in_slicer.py` | Instrucciones y ayuda para abrir y guardar la anotación. |
| `Open_HCC_in_Slicer.command` | Acceso para macOS; abre una instancia nueva de Slicer instalado en `/Applications/Slicer.app`. |

La carpeta `manager/` queda con el coordinador: contiene manifiesto de evaluación, candidata automática y soporte válido. **No entregar al lector `candidate.nrrd`, `candidate.json`, estadísticas, capturas ni comparaciones de Axivol.** Tampoco mostrar otras predicciones para que escoja una. No añadir la máscara automática a la escena de Slicer.

El lector puede consultar PRE para confirmar identidad, pero traza POST desde una segmentación vacía. Copiar, transformar o corregir la máscara Axivol produce una anotación asistida y potencialmente sesgada por anclaje; no es el brazo independiente. Si ya vio la salida, debe declarar `model_output_seen: true`; para una referencia independiente se necesita otro lector que no la haya visto.

## 2. Confirmar el objetivo antes de dibujar

Objetivo **propuesto**, pendiente de confirmación por el especialista: **lesión tratada completa en POST correspondiente a la etiqueta PRE `Mass`** (`whole_treated_lesion`). No equivale automáticamente a tejido viable ni a todo el depósito brillante de Lipiodol. El especialista debe acordar cómo tratar necrosis, depósito radiopaco, cápsula dudosa y vasos adyacentes. No ajustar el límite hasta que el volumen se parezca a PRE.

Comprobar caso, serie y adquisición en `case.json`. La cronología no confirma la fase de contraste. Si faltan fases necesarias o los artefactos impiden decidir el límite, registrar incertidumbre. PRE u otra fase pueden orientar la interpretación, pero no permiten copiar sus píxeles como contorno POST.

| `evaluability` | Cuándo usarlo |
| --- | --- |
| `delineable` | Se confirma la identidad y puede trazarse el objetivo acordado. Requiere máscara no vacía. |
| `uncertain` | El objetivo o sus límites no pueden confirmarse con suficiente confianza, incluida fase/imagen insuficiente. No forzar una máscara evaluable. |
| `not_identifiable` | No puede identificarse la lesión correspondiente. No significa ausencia tumoral. |
| `absence_confirmed` | El especialista confirma ausencia del objetivo según el protocolo. Requiere máscara vacía; nunca se deduce del fallo del algoritmo. |

Solo `delineable` y `absence_confirmed`, con las declaraciones requeridas, permiten una comparación formal. Los otros estados se conservan como incertidumbre; no se convierten en ceros ni se excluyen silenciosamente del estudio.

## 3. Delimitar en 3D Slicer

El flujo se probó en **3D Slicer 5.12.4 en macOS**, el 12 de noviembre de 2026: carga nativa de HCC_006, PRE adquisición 3 con `Mass` segmento 2 y POST adquisición 1; borrador vacío, Paint real, deshacer/rehacer exacto y exportación/reapertura sin cambios de píxeles ni geometría. Fue un trazo funcional, no una delineación anatómica: no se creó una referencia experta ni se calcularon métricas de exactitud. La documentación oficial describe [Segment Editor](https://slicer.readthedocs.io/en/latest/user_guide/modules/segmenteditor.html) y la [exportación a labelmap](https://slicer.readthedocs.io/en/latest/user_guide/modules/segmentations.html#export-segmentation-to-labelmap-volume).

1. **En macOS:** abrir `Open_HCC_in_Slicer.command` dentro de `reader/`. Requiere Slicer en `/Applications/Slicer.app` y abre una instancia nueva con las imágenes y un borrador POST vacío, sin borrar otra sesión. **Alternativa multiplataforma:** abrir una escena vacía y ejecutar lo siguiente en la consola Python de Slicer, sustituyendo la ruta:

   ```python
   import runpy
   kit = runpy.run_path('/ruta/al/paquete/reader/open_in_slicer.py')
   kit['open_case']()
   ```

2. En **Segment Editor**, comprobar que el volumen fuente sea la TC POST y que la segmentación POST empiece vacía. Mantener la geometría original: no cambiar resolución, recortar el volumen ni aplicar transformaciones. **PRE no está registrada a POST: mostrar `Published PRE Mass` únicamente sobre la TC PRE y ocultarla al volver a POST.** Consultar PRE cambiando el volumen visible; no reutilizar esa máscara como borrador POST.
3. Delimitar con herramientas manuales como **Draw**, **Paint** y **Erase**. Revisar cada corte incluido y los extremos superior e inferior. Comprobar también axial, coronal y sagital; una superficie 3D agradable no basta.
4. Si se emplea interpolación u otra ayuda, revisar su resultado en todos los cortes y registrar herramienta, versión y operación en una nota de revisión. No aceptar automáticamente regiones rellenadas ni ocultar límites inciertos.

## 4. Guardar y devolver

El retorno es **`POST-label.nrrd`**, un labelmap escalar 3D con valores **0 y 1**, misma forma, origen, orientación y espaciado que `POST_CT.nrrd`, más `reader_form.json`. No devolver solamente una captura, una superficie o una segmentación multicapa `.seg.nrrd`.

En la consola Python, si se abrió con el acceso de macOS:

```python
save_reference()
```

Si se abrió mediante `runpy`, usar en cambio:

```python
kit['save_reference']()
```

El ayudante exporta con POST como referencia a una carpeta nueva `return-…/`, sin sobrescribir versiones previas. Incluye `POST-label.nrrd`, una copia de `reader_form.json` con nombre/hash del archivo y `export.json` con versión de Slicer y procedencia técnica. Ese guardado no confirma identidad, experiencia ni revisión: completar honestamente el formulario de **la carpeta devuelta**, cuyos campos del lector siguen pendientes. El ayudante exige una escena inicial vacía y no borra trabajo existente.

**Alternativa manual:** en **Segmentations → Export/import models and labelmaps**, exportar solo el segmento objetivo a **Labelmap**. En **Advanced**, seleccionar `POST_CT` como **Reference volume**. Guardar el labelmap desde **Save** con nombre `POST-label.nrrd`. No aceptar un aviso de recorte sin averiguar por qué la segmentación sale de la imagen. El coordinador verificará binariedad, hashes y coincidencia física; no remuestreará silenciosamente una referencia incompatible.

Completar `reader_form.json` de la carpeta devuelta: identificador seudónimo del lector y formación real; experiencia hepática declarada; independencia y exposición al modelo; confirmación del objetivo e identidad; evaluabilidad y fecha real de revisión en UTC, formato ISO 8601 terminado en `Z` o `+00:00`. Las identidades y el hash de la TC proceden del paquete. `annotation_file` y `annotation_sha256` deben describir el archivo efectivamente entregado. No usar `true` o una fecha ficticia para desbloquear el evaluador. La declaración de experiencia no verifica credenciales por sí sola.

Conservar las anotaciones originales de cada lector. Cuando sea posible, obtener dos anotaciones independientes y adjudicar desacuerdos sin borrar sus versiones. HCC_002 y HCC_006 siguen siendo desarrollo; una referencia nueva de estos casos no los convierte en prueba de generalización.

El diseño sigue la recomendación de definir y documentar el estándar de referencia, formación de anotadores, herramientas y desacuerdos de **CLAIM 2024**, ítems 14–18: [texto primario](https://pmc.ncbi.nlm.nih.gov/articles/PMC11304031/), [DOI verificado](https://doi.org/10.1148/ryai.240300). Las decisiones de cegamiento y retorno pertenecen al protocolo específico de Axivol; no afirman que el paquete ya se haya validado.

Datos originales: Moawad et al., *HCC-TACE-Seg*, The Cancer Imaging Archive, 2021. [DOI 10.7937/TCIA.5FNA-0924](https://doi.org/10.7937/TCIA.5FNA-0924), licencia [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Estas copias se convirtieron de DICOM a NRRD conservando valores HU y geometría; el segmento PRE conserva la etiqueta publicada `Mass`. Las imágenes derivadas no son una nueva referencia POST ni una aprobación por sus autores.
