#!/usr/bin/env python3
"""Lanzador de compatibilidad — la implementación vive en oceankind/.

systemd (ExecStart) apunta aquí desde siempre; mantener este nombre evita
tocar la unidad de servicio en cada despliegue. El monolito que este archivo
reemplaza quedó preservado en legacy/superseded-monolith/.
"""

from oceankind.main import main

if __name__ == "__main__":
    main()
