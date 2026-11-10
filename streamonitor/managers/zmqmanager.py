import zmq
from streamonitor.manager import Manager
import streamonitor.log as log


class ZMQManager(Manager):
    def __init__(self, streamers):
        super().__init__(streamers)
        self.logger = log.Logger("manager_zmq")

    def run(self):
        with zmq.Context.instance().socket(zmq.REP) as socket:
            # Solo loopback: este socket acepta comandos de control sin
            # ninguna autenticación. Con "tcp://*" quedaba expuesto a internet.
            # Para controlarlo de forma remota, túnel SSH:
            #   ssh -L 6969:127.0.0.1:6969 <host>
            try:
                socket.bind("tcp://127.0.0.1:6969")
            except zmq.error.ZMQError as e:
                self.logger.error(
                    f'No se pudo abrir el puerto de control 6969 ({e}). '
                    'Lo normal es que ya haya otra instancia corriendo. '
                    'La grabación y la web siguen funcionando, pero '
                    'Controller.py no podrá dar órdenes.')
                return
            while True:
                line = socket.recv_string()
                self.logger.info("[ZMQ] " + line)
                reply = self.execCmd(line)
                if type(reply) is str:
                    socket.send_string(reply)
                else:
                    socket.send_string('')
