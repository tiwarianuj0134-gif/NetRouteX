import json
import logging
import math
import networkx as nx

from packet import Packet
from router import Router


logger = logging.getLogger(__name__)


class LSrouter(Router):
    """Link-state router that floods link advertisements and computes paths."""

    def __init__(self, addr, heartbeatTime):
        super().__init__(addr)
        self.heartbeatTime = heartbeatTime
        self.last_time = 0
        self.G = nx.DiGraph()
        self.G.add_node(str(addr))
        self.forward = {}
        self.linkToPort = {}
        self.portToLink = {}
        self.nodeToLastPacketSeqNum = {}
        self.seqNumLastSent = -1
        self.linkStates = {str(addr): self.packageLinks()["endpoints"]}

    def handlePacket(self, port, packet):
        if packet.isTraceroute():
            out_port = self.forward.get(packet.dstAddr)
            if out_port is not None:
                self.send(out_port, packet)
            return

        try:
            payload = json.loads(packet.content)
        except (TypeError, ValueError):
            logger.warning("Router %s ignored malformed link-state packet", self.addr)
            return

        origin = str(packet.srcAddr)
        sequence = payload.get("seqNum") if isinstance(payload, dict) else None
        endpoints = payload.get("endpoints") if isinstance(payload, dict) else None
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
            or not self._validEndpoints(endpoints)
        ):
            logger.warning("Router %s ignored invalid link-state data", self.addr)
            return
        if sequence <= self.nodeToLastPacketSeqNum.get(origin, -1):
            return

        self.nodeToLastPacketSeqNum[origin] = sequence
        self.linkStates[origin] = endpoints
        self._rebuildGraph()
        self.updateForwardTable()

        for neighbor_port, link in self.links.items():
            neighbor = link.e2 if link.e1 == self.addr else link.e1
            if neighbor_port != port and str(neighbor).isupper():
                self.send(
                    neighbor_port,
                    Packet(Packet.ROUTING, origin, neighbor, packet.content),
                )

    def handleNewLink(self, port, endpoint, _cost):
        endpoint = str(endpoint)
        self.linkToPort[endpoint] = port
        self.portToLink[port] = endpoint
        self._refreshLocalLinkState()
        self._rebuildGraph()
        self.updateForwardTable()
        self.broadcast()

    def handleRemoveLink(self, port):
        endpoint = self.portToLink.pop(port, None)
        if endpoint is not None:
            self.linkToPort.pop(endpoint, None)
        self._refreshLocalLinkState()
        self._rebuildGraph()
        self.updateForwardTable()
        self.broadcast()

    def handleTime(self, timeMillisecs):
        if timeMillisecs - self.last_time >= self.heartbeatTime:
            self.last_time = timeMillisecs
            self.broadcast()

    def _validEndpoints(self, endpoints):
        if not isinstance(endpoints, dict):
            return False
        for endpoint, costs in endpoints.items():
            if not isinstance(endpoint, str) or not isinstance(costs, dict):
                return False
            for field in ("costTo", "costFrom"):
                cost = costs.get(field)
                if (
                    not isinstance(cost, (int, float))
                    or isinstance(cost, bool)
                    or not math.isfinite(cost)
                    or cost < 0
                ):
                    return False
        return True

    def _refreshLocalLinkState(self):
        self.linkStates[str(self.addr)] = self.packageLinks()["endpoints"]

    def _rebuildGraph(self):
        graph = nx.DiGraph()
        graph.add_node(str(self.addr))
        for origin, endpoints in self.linkStates.items():
            graph.add_node(origin)
            for endpoint, costs in endpoints.items():
                if endpoint.isupper():
                    peer_endpoints = self.linkStates.get(endpoint, {})
                    if origin in peer_endpoints:
                        graph.add_edge(origin, endpoint, cost=costs["costTo"])
                else:
                    graph.add_edge(origin, endpoint, cost=costs["costTo"])
                    graph.add_edge(endpoint, origin, cost=costs["costFrom"])
        self.G = graph

    def updateForwardTable(self):
        forward = {}
        for target in self.G.nodes:
            if target == str(self.addr) or target.isupper():
                continue
            try:
                path = nx.shortest_path(
                    self.G, source=str(self.addr), target=target, weight="cost"
                )
            except nx.NetworkXNoPath:
                continue
            if len(path) > 1 and path[1] in self.linkToPort:
                forward[target] = self.linkToPort[path[1]]
        self.forward = forward

    def broadcast(self):
        self.seqNumLastSent += 1
        payload = self.packageLinks()
        payload["seqNum"] = self.seqNumLastSent
        content = json.dumps(payload)
        self.nodeToLastPacketSeqNum[str(self.addr)] = self.seqNumLastSent
        for port, link in self.links.items():
            neighbor = link.e2 if link.e1 == self.addr else link.e1
            if str(neighbor).isupper():
                self.send(port, Packet(Packet.ROUTING, self.addr, neighbor, content))

    def packageLinks(self):
        return {"src": str(self.addr), "endpoints": self.getNeighbors()}

    def getNeighbors(self):
        neighbors = {}
        for link in self.links.values():
            if link.e1 == self.addr:
                endpoint = str(link.e2)
                cost_to = link.l12 / link.latencyMultiplier
                cost_from = link.l21 / link.latencyMultiplier
            else:
                endpoint = str(link.e1)
                cost_to = link.l21 / link.latencyMultiplier
                cost_from = link.l12 / link.latencyMultiplier
            neighbors[endpoint] = {"costTo": cost_to, "costFrom": cost_from}
        return neighbors

    def debugString(self):
        edges = "\n".join(nx.generate_edgelist(self.G, data=True))
        return (
            f"{edges}\n"
            f"seqNumLastSeenByNodes: {self.nodeToLastPacketSeqNum}\n"
            f"linkStates: {self.linkStates}\n"
            f"forward table: {self.forward}\n"
        )
