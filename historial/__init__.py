"""Adaptador de historial: lee el archivo de pagos de un comercio, lo traduce a la API y mide qué habría detectado el sistema.

Es el «Camino A» del plan de piloto: no toca la operación del comercio ni la base real; reproduce los pagos en orden de fecha
sobre una base temporal que se borra al terminar, con el mismo servicio que usa la API.
"""
