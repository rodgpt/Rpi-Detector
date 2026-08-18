# MAR FUTURA / OCEANKIND
## Nota de alcance: Fase 1 (20 días hábiles) y Fase 2

Anexo al presupuesto acordado
Agosto 2026

---

## 1. Por qué esta nota

Al iniciar el trabajo hicimos una verificación línea por línea del código en producción, más detallada que la de los informes anteriores. Aparecieron cuatro defectos que no estaban en ninguno de los informes entregados, y tres de ellos pueden dejar el sistema sin detectar nada sin emitir ninguna señal de error.

Esto cambia el orden de prioridades. Esta nota fija qué entrega la Fase 1 dentro de los 20 días hábiles acordados, qué pasa a una Fase 2, y qué necesitamos de Mar Futura para arrancar.

El precio y el plazo de la Fase 1 no cambian.

---

## 2. Hallazgos nuevos que reordenan el trabajo

**2.1 El modo de detección de respaldo no puede generar alertas.** El sistema documenta un modo `rms` como alternativa segura si falla el modelo de machine learning. Ese modo está en el código pero la decisión de alerta nunca lo consulta. Configurar el sistema en modo `rms`, creyendo que es la opción conservadora, apaga la detección por completo.

Lo que hace difícil advertirlo en terreno es que todo lo demás sigue funcionando con normalidad. El mensaje de "sistema activo" se sigue enviando cada 12 horas, y las alertas de batería baja y de recuperación se siguen enviando por el mismo canal de WhatsApp en cada heartbeat, con independencia del modo de detección. El operador recibe tráfico regular y de apariencia correcta desde una unidad que perdió la capacidad de emitir una alerta de detección.

**2.2 El umbral de detección remoto no tiene efecto.** El valor `alert_threshold` que se envía por configuración remota se aplica a una variable que no participa en la decisión de alerta. Se puede cambiar el umbral, verlo reflejado en el estado del sistema, y no alterar la sensibilidad real. Los dos valores que sí deciden solo se pueden cambiar editando el archivo de entorno del equipo y reiniciando, es decir, mediante una actualización remota, que es justamente la operación que hoy puede dejar la unidad inutilizable.

**2.3 Las detecciones dentro del período de espera se pierden y no se limpian.** Cuando ocurre una detección dentro de los 10 minutos posteriores a una alerta previa, el evento no se sube, no queda registrado en el historial, no incrementa el contador, y el archivo de audio tampoco se elimina. Esto tiene dos consecuencias. La primera es de integridad de datos: una secuencia de explosiones queda registrada como un solo evento, de modo que cualquier estadística de frecuencia derivada del historial subestima, y subestima justamente en los episodios más relevantes. La segunda es operativa: los clips se acumulan sin ningún mecanismo de limpieza, ni en el código ni en el sistema operativo. Los clips que sí generan alerta tampoco se eliminan. El script de protección de la tarjeta SD redirige el directorio de clips a memoria RAM, lo cual es correcto para proteger la tarjeta y se vuelve un problema al combinarse con la ausencia de limpieza. Cada clip ocupa cerca de 1 MB, y las detecciones suprimidas no están limitadas por ninguna frecuencia máxima: llegan hasta una por ciclo, es decir, aproximadamente 11 MB por minuto en el peor caso. Sobre una unidad con 2 GB de RAM, un período sostenido de falsos positivos por lluvia o motores cercanos, que el propio código reconoce que ocurren, agota la memoria en pocas horas.

**2.4 La ventana sorda es peor justo cuando importa.** El sistema deja de escuchar mientras procesa y transmite. Ese tiempo muerto no es uniforme: es más largo inmediatamente después de una detección, porque ahí se suman el envío de WhatsApp, la subida del clip y la reescritura del historial completo. La pesca con explosivos no produce eventos aislados, produce secuencias. El sistema está más ciego en los segundos siguientes a la primera detección, y luego descarta durante 10 minutos lo que alcanza a captar.

Ninguno de estos cuatro puntos requiere infraestructura nueva. Los cuatro se corrigen en la primera semana.

---

## 3. Qué entrega la Fase 1

### 3.1 Arquitectura de software (ítem 1 del presupuesto)

Se entrega completo.

- Unificación de las dos bases de código en una sola, con corrección del script de aprovisionamiento y del archivo de dependencias. Hoy una instalación nueva desde el repositorio produce una unidad que no funciona: el script instala la versión obsoleta y el archivo de dependencias omite todas las librerías que el sistema en producción necesita.
- Modularización del monolito en componentes con responsabilidad única.
- Pipeline asíncrona: la captura de audio nunca se detiene. La detección y la transmisión corren en paralelo sobre colas acotadas, con política explícita de descarte y contador de descartes publicado en el estado del sistema.
- Actualización remota con particiones A/B, verificación de salud posterior al reinicio y reversión automática. La reversión se demuestra provocando deliberadamente una actualización fallida sobre una unidad de banco de pruebas, no sobre el equipo desplegado.
- Corrección de los cuatro defectos de la sección 2.
- Falla ruidosa: si el modelo no carga, el sistema lo informa por WhatsApp y lo marca en el estado, en lugar de quedar mudo.

### 3.2 Seguridad (ítem 2.1 del presupuesto)

Se entrega completo, mediante el mecanismo más directo y no mediante una API intermedia.

- Rotación de las credenciales de Twilio y eliminación de las mismas del código fuente, del archivo de respaldo y del bytecode compilado.
- Coordenadas del sensor fuera del código fuente.
- Contenedor de almacenamiento en modo privado. El dashboard pasa a leer mediante un token de solo lectura de alcance acotado y vigencia limitada. El equipo pasa a escribir con un token de escritura acotado, en lugar de la clave de la cuenta de almacenamiento.
- Configuración remota firmada y con rangos acotados, y exposición de los parámetros que realmente controlan la sensibilidad.

El resultado es que ningún dato del sistema queda accesible sin credencial, y ninguna credencial queda en el código.

### 3.3 Escalabilidad (ítem 2.2 del presupuesto)

Se entrega la base, no la totalidad.

- Reorganización del almacenamiento por dispositivo, de modo que cada unidad escribe en su propio espacio.
- Eliminación de la condición de carrera del historial compartido, reemplazando el patrón de lectura, modificación y reescritura por registros de evento independientes. Con esto la segunda unidad no puede sobrescribir los datos de la primera.
- Dashboard con selector de dispositivo, consulta acotada por ventana temporal y listado paginado.

Con esto, cuando llegue la segunda unidad se conecta y funciona.

---

## 4. Qué pasa a la Fase 2

Los siguientes puntos del presupuesto exceden lo ejecutable dentro de los 20 días hábiles acordados y se entregan como especificación y cotización el día 20:

- API backend como servicio independiente.
- Usuarios con reglas de acceso diferenciadas.
- Panel de administración para añadir y quitar usuarios y asignar dispositivos.
- Autenticación de acceso al dashboard.

Estimación de la Fase 2: 65 a 103 horas.

La razón por la que estos puntos se separan y no se comprimen es la siguiente. Una capa de autenticación construida a medias es peor que ninguna, porque produce la impresión de estar protegido sin estarlo. Un dashboard migrado a medias deja de funcionar el que hoy funciona. La Fase 1 entrega la seguridad efectiva por la vía corta y deja la infraestructura de usuarios para cuando haya tiempo de construirla bien, con margen suficiente antes de la llegada de las próximas unidades.

---

## 5. Qué necesitamos de Mar Futura

**5.1 Accesos, con fecha.** El ítem de seguridad no puede comenzar sin permisos sobre la suscripción de Azure. Se requiere rol de Contributor sobre el grupo de recursos más Storage Blob Data Owner, o bien Owner. Adicionalmente se requiere acceso a la consola de Twilio para rotar el token.

Las credenciales de Twilio están hoy en el código fuente, en un archivo de respaldo y en el bytecode compilado, y el proceso de actualización remota sincroniza los tres contra un repositorio remoto. La rotación es la acción más urgente del proyecto entero y depende enteramente de este acceso.

Dejamos constancia de que la fecha de entrega de la sección de seguridad se desplaza en la misma medida en que se demore la concesión de estos accesos.

**5.2 Una consulta de diagnóstico.** ¿El gráfico de energía del dashboard muestra datos actualmente? La respuesta indica cuál de dos situaciones está ocurriendo en el equipo desplegado. Si muestra datos, la protección de la tarjeta SD no está activa. Si está vacío, la protección está activa y la telemetría se viene descartando desde entonces. Ambas se corrigen, pero se corrigen distinto.

**5.3 Reuniones.** Las reuniones y correcciones forman parte de las horas declaradas. Sobre un total de 36 a 40 horas proponemos fijar 4 horas incluidas para reuniones y revisiones, y facturar las adicionales a la misma tarifa. Esto protege el tiempo de construcción sin cambiar el precio acordado.

---

## 6. Criterios de aceptación

Cada ítem se considera entregado contra un resultado medible.

| Ítem | Criterio |
|---|---|
| Pipeline asíncrona | Ciclo de escucha superior al 99% medido sobre 24 horas continuas, con el valor publicado en el estado del sistema |
| Actualización remota | Reversión automática demostrada sobre una actualización fallida provocada deliberadamente, con registro |
| Unificación de código | Aprovisionamiento desde cero sobre hardware limpio produce una unidad operativa sin intervención manual |
| Seguridad | Toda solicitud sin credencial al contenedor responde con error de autorización; ninguna credencial presente en el código fuente |
| Falla ruidosa | Fallo inducido en la carga del modelo genera notificación en menos de un ciclo de heartbeat |
| Base multi dispositivo | Dos dispositivos simulados escribiendo en paralelo durante una hora sin pérdida de eventos |

---

## 7. Calendario

| Semana | Contenido |
|---|---|
| 1 | Solicitud de accesos. Credenciales y coordenadas fuera del código. Corrección de los cuatro defectos de la sección 2. Instrumentación y medición del ciclo de escucha. Unificación de bases de código y aprovisionamiento. |
| 2 | Pipeline asíncrona. Pruebas de resistencia sobre unidad de banco. |
| 3 | Actualización remota A/B con reversión demostrada. Despliegue en producción y observación. Cierre de seguridad en Azure. |
| 4 | Configuración remota firmada. Base multi dispositivo. Dashboard con selector de dispositivo. Entrega, manual de operación y especificación de Fase 2. |

---

*Futurity Systems*
