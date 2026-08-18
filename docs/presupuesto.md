**MAR FUTURA**

Todos los ítems estñán en horas para facilitar el cálculo del costo, pero realistamente puedo trabajar un par de horas cada día, por lo que los días totales van a ser algo así como: horas/2 = días

1\. Arquitectura de Software

  1.1 Modularización y unificación de código

    Unificar ambas codebases, modularizar el código (En vez del monolito) permite mejor detección y recuperación de errores, permite el punto siguiente.

  1.2 Brechas de Detección (Ventana Sorda)

    Habiendo modularizado el código, se puede establecer una rutina asíncrona que procese el audio, mientras la detección sigue corriendo. La detección graba clip y lo pone en cola hasta que el procesamiento del clip anterior termine, asi se pueden procesar eventos seguidos sin bloquear el software.

  1.3 Riesgo de Actualización OTA

    El mecanismo OTA puede transformarse en uno con red de seguridad, sólo una vez que el software se ha descargado por completo se puede actualizar el código, y ante cualquier inicio del sistema en el que el nuevo software no cumpla con un health check, automáticamente se vuelve a la última versión funcional.

  **Esfuerzo total** 8-12 horas

2\. Dashboard y Escalabilidad, backend con API.

  2.1 Seguridad

    Una API permite deshacerse de los problemas de seguridad a través de autenticación (Credenciales de Twilio, Blob público de Azure y coordenadas de dispositivos no encriptadas). 
    
  2.2 Escalabilidad - Soporte multi dispositivo

    Crear usuarios y dispositivos distintos, con distintas reglas de acceso. (Qué usuarios ven qué dispositivos debe ser definido en el futuro)
    Crear blobs para monitorear y mostrar múltiples dispositivos por separado. (Actualmente todos los datos viven en una misma tabla que se descarga por completo cada vez)
    Panel de administración para añadir/quitar usuarios, asignar dispositivos, etc...

  **Esfuerzo total** 24-28 horas


3\. Resumen

  --------- ------------------------------------------ ----------------- -----------------------------------------------------------------------
  **§**     **Ítem**                                   **Esfuerzo**      **Valoración Honesta**

  1.1       Modularización y unificación de código      8-12 horas        Hace más fácil mantener el código, permite funciones asíncronas.
  1.2       Brechas de Detección (Ventana Sorda)                          Permite escuchar el 100% del tiempo.
  1.3       Riesgo de Actualización OTA                                   Evita que un dispositivo quede inutilizado.
  
  2.1       Seguridad                                   24-28 horas       Imposible escalar sin seguridad
  2.2       Escalabilidad - Soporte multi dispositivo                     Permite añadir usuarios y dispositivos al sistema

  --------- ------------------------------------------ ----------------- -----------------------------------------------------------------------


4\. Condiciones

  20 días hábiles de trabajo

  ( 36 - 40 horas ) ( 1,1525 UF por hora ) = $1,696,000 - $1,884,000

  Las reuniones y correcciones son parte de las horas declaradas.

   