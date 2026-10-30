import logging
import math
from router import Router
from packet import Packet
from json import dumps, loads


logger = logging.getLogger(__name__)


class DVrouter(Router):
    """Distance vector routing protocol implementation."""

    def __init__(self, addr, heartbeatTime):
        Router.__init__(self, addr)
        self.heartbeatTime = heartbeatTime
        self.last_time = 0
        self.dv = {}  # {destination: (cost, port)}
        self.neighbor_dvs = {}  # {neighbor: {destination: cost}}
        self.port_to_neighbor = {}  # {port: neighbor}
        self.neighbor_to_port = {}  # {neighbor: port}
        self.forward = {}  # {destination: port}
        self.cost_to_neighbor = {}  # {neighbor: cost}
        self.addr = addr

    def handlePacket(self, port, packet):
        if packet.isTraceroute():
            # Forward data packet if possible
            dst = packet.dstAddr
            if dst in self.forward:
                out_port = self.forward[dst]
                self.send(out_port, packet)
        else:
            # Routing packet: update neighbor's DV and recalculate
            neighbor = self.port_to_neighbor.get(port)
            if neighbor is None:
                return
            try:
                neighbor_dv = loads(packet.content)
            except (TypeError, ValueError):
                logger.warning("Router %s ignored malformed distance-vector packet", self.addr)
                return
            if not isinstance(neighbor_dv, dict) or any(
                not isinstance(destination, str)
                or not isinstance(cost, (int, float))
                or isinstance(cost, bool)
                or math.isnan(cost)
                or cost < 0
                for destination, cost in neighbor_dv.items()
            ):
                logger.warning("Router %s ignored invalid distance-vector data", self.addr)
                return
            self.neighbor_dvs[neighbor] = neighbor_dv
            changed = self.update_distance_vector()
            if changed:
                self.broadcast_dv()


    def handleNewLink(self, port, endpoint, cost):
        neighbor = endpoint
        self.port_to_neighbor[port] = neighbor
        self.neighbor_to_port[neighbor] = port
        self.cost_to_neighbor[neighbor] = cost
        # Initialize neighbor's DV as empty
        if neighbor not in self.neighbor_dvs:
            self.neighbor_dvs[neighbor] = {}
        # Set direct cost to neighbor
        if neighbor not in self.dv or cost < self.dv.get(neighbor, (float('inf'), None))[0]:
            self.dv[neighbor] = (cost, port)
        self.update_distance_vector()
        self.broadcast_dv()


    def handleRemoveLink(self, port):
        neighbor = self.port_to_neighbor.pop(port, None)
        if neighbor is not None:
            self.neighbor_to_port.pop(neighbor, None)
            self.neighbor_dvs.pop(neighbor, None)
            self.cost_to_neighbor.pop(neighbor, None)
        self.update_distance_vector()
        self.broadcast_dv()


    def handleTime(self, timeMillisecs):
        if timeMillisecs - self.last_time >= self.heartbeatTime:
            self.last_time = timeMillisecs
            self.broadcast_dv()


    def debugString(self):
        return f"DV: {self.dv}\nForward: {self.forward}\nNeighbors: {self.port_to_neighbor}"

    def update_distance_vector(self):
        # Rebuild from current advertisements so withdrawn or more expensive
        # routes do not remain in the table.
        new_dv = {self.addr: (0, None)}
        for neighbor, port in self.neighbor_to_port.items():
            new_dv[neighbor] = (self.cost_to_neighbor[neighbor], port)

        for neighbor, neighbor_dv in self.neighbor_dvs.items():
            if neighbor not in self.neighbor_to_port:
                continue
            cost_to_neighbor = self.cost_to_neighbor[neighbor]
            port = self.neighbor_to_port[neighbor]
            for dst, neighbor_cost in neighbor_dv.items():
                if dst == self.addr or dst == neighbor or not math.isfinite(neighbor_cost):
                    continue
                total_cost = cost_to_neighbor + neighbor_cost
                if (
                    dst not in new_dv
                    or total_cost < new_dv[dst][0]
                    or (total_cost == new_dv[dst][0] and port < new_dv[dst][1])
                ):
                    new_dv[dst] = (total_cost, port)

        changed = new_dv != self.dv
        self.dv = new_dv
        self.forward = {dst: port for dst, (_, port) in self.dv.items() if port is not None}
        return changed

    def broadcast_dv(self):
        # Send our DV to all neighbors
        dv_to_send = {dst: cost for dst, (cost, _) in self.dv.items()}
        for port, neighbor in self.port_to_neighbor.items():
            poisoned_dv = dv_to_send.copy()
            for dst, (_, route_port) in self.dv.items():
                if route_port == port and dst != neighbor:
                    poisoned_dv[dst] = float('inf')
            pkt = Packet(Packet.ROUTING, self.addr, neighbor, dumps(poisoned_dv))
            self.send(port, pkt)
