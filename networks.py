import sys
import threading
import json
import pickle
import signal
import time
import math
import queue as Queue
from collections import defaultdict
from client import Client
from link import Link
from router import Router
# DVRouter and LSRouter imports placed in main and conditioned by DV|LS
# argument so a syntax error in one of the files will not prevent the other
# from being tested

def json_load_byteified(file_handle):
    return _byteify(
        json.load(file_handle, object_hook=_byteify),
        ignore_dicts=True
    )

def _byteify(data, ignore_dicts = False):
    # if this is a text string, return it as-is (Python 3 uses str)
    if isinstance(data, str):
        return data
    # if this is a list of values, return list of byteified values
    if isinstance(data, list):
        return [ _byteify(item, ignore_dicts=True) for item in data ]
    # if this is a dictionary, return dictionary of byteified keys and values
    # but only if we haven't already byteified it
    if isinstance(data, dict) and not ignore_dicts:
        return {
            _byteify(key, ignore_dicts=True): _byteify(value, ignore_dicts=True)
            for key, value in data.items()
        }
    # if it's anything else, return it in its original form
    return data

class Network:
    """Network class maintains all clients, routers, links, and confguration"""

    def __init__(self, netJsonFilepath, routerClass, visualize=False):
        """Create a new network from the parameters in the file at
           netJsonFilepath.  routerClass determines whether to use DVrouter,
           LSrouter, or the default Router"""

        # parse configuration details
        with open(netJsonFilepath, 'r', encoding='utf-8') as netJsonFile:
            netJson = json_load_byteified(netJsonFile)
        self._validateNetwork(netJson)
        self.latencyMultiplier = 100
        self.endTime = netJson["endTime"] * self.latencyMultiplier
        self.visualize = visualize
        if visualize:
            self.latencyMultiplier *= netJson["visualize"]["timeMultiplier"]
        self.clientSendRate = netJson["clientSendRate"]*self.latencyMultiplier

        # parse and create routers, clients, and links
        self.routers = self.parseRouters(netJson["routers"], routerClass)
        self.clients = self.parseClients(netJson["clients"], self.clientSendRate)
        self.links = self.parseLinks(netJson["links"])
        self.allLinks = [link_data[4] for link_data in self.links.values()]

        # parse link changes
        if "changes" in netJson:
            self.changes = self.parseChanges(netJson["changes"])
        else:
            self.changes = None

        # parse correct routes and create some tracking fields
        self.correctRoutes = self.parseCorrectRoutes(netJson["correctRoutes"])
        self.threads = []
        self.routes = {}
        #self.routesLock = thread.allocate_lock()
        self.routesLock = threading.Lock()

    @staticmethod
    def _validateNetwork(config):
        if not isinstance(config, dict):
            raise ValueError("topology must contain a JSON object")

        required = ("routers", "clients", "clientSendRate", "endTime", "links", "correctRoutes")
        missing = [field for field in required if field not in config]
        if missing:
            raise ValueError("topology is missing required field(s): " + ", ".join(missing))

        routers = config["routers"]
        clients = config["clients"]
        if not isinstance(routers, list) or not isinstance(clients, list):
            raise ValueError("'routers' and 'clients' must be lists")
        nodes = routers + clients
        if any(not isinstance(node, str) or not node for node in nodes):
            raise ValueError("router and client addresses must be non-empty strings")
        if len(set(nodes)) != len(nodes):
            raise ValueError("router and client addresses must be unique")
        if set(routers) & set(clients):
            raise ValueError("a node cannot be both a router and a client")
        if not routers:
            raise ValueError("topology must contain at least one router")

        for field in ("clientSendRate", "endTime"):
            value = config[field]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"'{field}' must be a positive finite number")

        links = config["links"]
        if not isinstance(links, list):
            raise ValueError("'links' must be a list")
        used_ports = {router: set() for router in routers}
        client_connections = {client: 0 for client in clients}
        seen_pairs = set()
        for index, link in enumerate(links):
            if not isinstance(link, list) or len(link) != 6:
                raise ValueError(f"link {index} must contain exactly six fields: [node1, node2, port1, port2, cost12, cost21]")
            node1, node2, port1, port2, cost12, cost21 = link
            if node1 not in nodes or node2 not in nodes or node1 == node2:
                raise ValueError(f"link {index} references an unknown or identical endpoint")
            if node1 not in routers and node2 not in routers:
                raise ValueError(f"link {index} must connect at least one router")
            pair = frozenset((node1, node2))
            if pair in seen_pairs:
                raise ValueError(f"duplicate link between {node1} and {node2}")
            seen_pairs.add(pair)
            for node, port in ((node1, port1), (node2, port2)):
                if node in routers:
                    if not isinstance(port, int) or isinstance(port, bool) or port < 0:
                        raise ValueError(f"router port on link {index} must be a non-negative integer")
                    if port in used_ports[node]:
                        raise ValueError(f"router {node} uses port {port} more than once")
                    used_ports[node].add(port)
                else:
                    client_connections[node] += 1
            for cost in (cost12, cost21):
                if (
                    not isinstance(cost, (int, float))
                    or isinstance(cost, bool)
                    or not math.isfinite(cost)
                    or cost < 0
                ):
                    raise ValueError(f"link costs on link {index} must be finite and non-negative")
        if any(count > 1 for count in client_connections.values()):
            raise ValueError("each client can be connected to at most one link")

        routes = config["correctRoutes"]
        if not isinstance(routes, list):
            raise ValueError("'correctRoutes' must be a list")
        for index, route in enumerate(routes):
            if (
                not isinstance(route, list)
                or len(route) < 2
                or any(not isinstance(node, str) or node not in nodes for node in route)
            ):
                raise ValueError(f"correct route {index} must be a node path with known addresses")

        changes = config.get("changes", [])
        if not isinstance(changes, list):
            raise ValueError("'changes' must be a list")
        for index, event in enumerate(changes):
            if not isinstance(event, list) or len(event) != 3:
                raise ValueError(f"change {index} must be [time, link, 'up'|'down']")
            event_time, target, action = event
            if (
                not isinstance(event_time, (int, float))
                or isinstance(event_time, bool)
                or not math.isfinite(event_time)
                or event_time < 0
            ):
                raise ValueError(f"change {index} time must be a finite non-negative number")
            if action == "down":
                if (
                    not isinstance(target, list)
                    or len(target) != 2
                    or target[0] not in routers
                    or target[1] not in routers
                ):
                    raise ValueError(f"change {index} down target must be a pair of router addresses")
            elif action == "up":
                if not isinstance(target, list) or len(target) != 6:
                    raise ValueError(f"change {index} up target must be a six-field router link")
                node1, node2, port1, port2, cost12, cost21 = target
                if node1 not in routers or node2 not in routers or node1 == node2:
                    raise ValueError(f"change {index} up target must connect two distinct routers")
                for port in (port1, port2):
                    if not isinstance(port, int) or isinstance(port, bool) or port < 0:
                        raise ValueError(f"change {index} router ports must be non-negative integers")
                for cost in (cost12, cost21):
                    if (
                        not isinstance(cost, (int, float))
                        or isinstance(cost, bool)
                        or not math.isfinite(cost)
                        or cost < 0
                    ):
                        raise ValueError(f"change {index} link costs must be finite and non-negative")
            else:
                raise ValueError(f"change {index} action must be 'up' or 'down'")


    def parseRouters(self, routerParams, routerClass):
        """Parse routes from routerParams dict"""
        routers = {}
        for addr in routerParams:
            #print "Router {}".format(addr)
            routers[addr] = routerClass(addr, heartbeatTime=self.latencyMultiplier*10)
        return routers


    def parseClients(self, clientParams, clientSendRate):
        """Parse clients from clientParams dict"""
        clients = {}
        for addr in clientParams:
            #print "Client {}".format(addr)
            clients[addr] = Client(addr, clientParams, clientSendRate, self.updateRoute)
        return clients


    def parseLinks(self, linkParams):
        """Parse links from linkParams, dict"""
        links = {}
        for addr1, addr2, p1, p2, c12, c21 in linkParams:
            #print "{}:{} --cost:{}--> {}:{} --cost:{}--> {}:{}".format(
                   #addr1, p1, c12, addr2, p2, c21, addr1, p1)
            link = Link(addr1, addr2, c12, c21, self.latencyMultiplier)
            links[(addr1,addr2)] = (p1, p2, c12, c21, link)
        return links


    def parseChanges(self, changesParams):
        """Parse link changes from changesParams dict"""
        changes = Queue.PriorityQueue()
        for change in changesParams:
            #print change
            changes.put(change)
        return changes


    def parseCorrectRoutes(self, routesParams):
        """parse correct routes, from routesParams dict"""
        correctRoutes = defaultdict(list)
        for route in routesParams:
            src, dst = route[0], route[-1]
            correctRoutes[(src,dst)].append(route)
        return correctRoutes


    def run(self):
        """Run the network.  Start threads for each client and router. Start
           thread to track link changes.  If not visualizing, wait until
           end time and then print final routes"""
        for router in self.routers.values():
            thread = router_thread(router)
            thread.start()
            self.threads.append(thread)
        for client in self.clients.values():
            thread = client_thread(client)
            thread.start()
            self.threads.append(thread)
        self.addLinks()
        if self.changes:
            self.handleChangesThread = handle_changes_thread(self)
            self.handleChangesThread.start()

        if not self.visualize:
            signal.signal(signal.SIGINT, self.handleInterrupt)
            time.sleep(self.endTime/float(1000))
            self.finalRoutes()
            sys.stdout.write("\n"+self.getRouteString()+"\n")
            self.joinAll()



    def addLinks(self):
        """Add links to clients and routers"""
        for addr1, addr2 in self.links:
            p1, p2, c12, c21, link = self.links[(addr1, addr2)]
            if addr1 in self.clients:
                self.clients[addr1].changeLink(("add", link))
            if addr2 in self.clients:
                self.clients[addr2].changeLink(("add", link))
            if addr1 in self.routers:
                self.routers[addr1].changeLink(("add", p1, addr2, link, c12))
            if addr2 in self.routers:
                self.routers[addr2].changeLink(("add", p2, addr1, link, c21))


    def handleChanges(self):
        """Handle changes to links. Run this method in a separate thread.
           Uses a priority queue to track time of next change"""
        startTime = time.time()*1000
        while not self.changes.empty():
            changeTime, target, change = self.changes.get()
            currentTime = time.time()*1000
            waitTime = (changeTime*self.latencyMultiplier + startTime) - currentTime
            if waitTime > 0:
                time.sleep(waitTime/float(1000))
            # link changes
            if change == "up":
                addr1, addr2, p1, p2, c12, c21 = target
                link = Link(addr1, addr2, c12, c21, self.latencyMultiplier)
                self.allLinks.append(link)
                self.links[(addr1,addr2)] = (p1, p2, c12, c21, link)
                self.routers[addr1].changeLink(("add", p1, addr2, link, c12))
                self.routers[addr2].changeLink(("add", p2, addr1, link, c21))
            elif change == "down":
                addr1, addr2, = target
                link_key = (addr1, addr2)
                if link_key not in self.links:
                    link_key = (addr2, addr1)
                if link_key not in self.links:
                    raise ValueError(f"cannot take down link {addr1}-{addr2}: link is not active")
                p1, p2, _, _, link = self.links.pop(link_key)
                if link_key != (addr1, addr2):
                    p1, p2 = p2, p1
                self.routers[addr1].changeLink(("remove", p1))
                self.routers[addr2].changeLink(("remove", p2))
            # update visualization
            if hasattr(Network, "visualizeChangesCallback"):
                Network.visualizeChangesCallback(change, target)


    def updateRoute(self, src, dst, route):
        """Callback function used by clients to update the
           current routes taken by traceroute packets"""
        self.routesLock.acquire()
        timeMillisecs = int(round(time.time() * 1000))
        isGood = route in self.correctRoutes[(src,dst)]
        try:
            _, _, currentTime = self.routes[(src,dst)]
            if timeMillisecs > currentTime:
                self.routes[(src,dst)] = (route, isGood, timeMillisecs)
        except KeyError:
            self.routes[(src,dst)] = (route, isGood, timeMillisecs)
        finally:
            self.routesLock.release()


    def getRouteString(self, labelIncorrect=True):
        """Create a string with all the current routes found by traceroute
           packets and whether they are correct"""
        self.routesLock.acquire()
        routeStrings = []
        allCorrect = True
        for src,dst in self.routes:
            route, isGood, _ = self.routes[(src,dst)]
            routeStrings.append("{} -> {}: {} {}".format(src, dst, route,
                "" if (isGood or not labelIncorrect) else "Incorrect Route"))
            if not isGood:
                allCorrect = False
        routeStrings.sort()
        if allCorrect and len(self.routes) > 0:
            routeStrings.append("\nSUCCESS: All Routes correct!")
        else:
            routeStrings.append("\nFAILURE: Not all routes are correct")
        routeString = "\n".join(routeStrings)
        self.routesLock.release()
        return routeString


    def getRoutePickle(self):
        """Create a pickle with the current routes
           found by traceroute packets"""
        self.routesLock.acquire()
        routePickle = pickle.dumps(self.routes)
        self.routesLock.release()
        return routePickle


    def resetRoutes(self):
        """Reset the routes foudn by traceroute packets"""
        self.routesLock.acquire()
        self.routes = {}
        self.routesLock.release()


    def finalRoutes(self):
        """Have the clients send one final batch of traceroute packets"""
        self.resetRoutes()
        for client in self.clients.values():
            client.lastSend()
        time.sleep(4*self.clientSendRate/float(1000))

    def joinAll(self):
        if self.changes:
            self.handleChangesThread.join()
        for thread in self.threads:
            thread.join()
        for link in self.allLinks:
            link.stopPendingSends()

    def handleInterrupt(self, signum, _):
        self.joinAll()
        print('')
        quit()



def main():
    """Main function parses command line arguments and runs network"""
    if len(sys.argv) not in (2, 3):
        print("Usage: python networks.py <topology.json> [DV|LS]", file=sys.stderr)
        return 2
    netCfgFilepath = sys.argv[1]
    mode = sys.argv[2].upper() if len(sys.argv) == 3 else "DV"
    if mode not in ("DV", "LS"):
        print("Error: routing mode must be DV or LS", file=sys.stderr)
        return 2

    routerClass = Router
    try:
        if mode == "DV":
            from distance_vector_router import DVrouter
            routerClass = DVrouter
        else:
            from link_state_router import LSrouter
            routerClass = LSrouter
        net = Network(netCfgFilepath, routerClass, visualize=False)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    net.run()
    return 0

# Extensions of threading.Thread class

class router_thread(threading.Thread):

    def __init__(self, router):
        threading.Thread.__init__(self)
        self.router = router

    def run(self):
        self.router.runRouter()

    def join(self, timeout=None):
        # Terrible style (think about changing) but works like a charm
        self.router.keepRunning = False
        super(router_thread, self).join(timeout)

class client_thread(threading.Thread):

    def __init__(self, client):
        threading.Thread.__init__(self)
        self.client = client

    def run(self):
        self.client.runClient()

    def join(self, timeout=None):
        # Terrible style (think about changing) but works like a charm
        self.client.keepRunning = False
        super(client_thread, self).join(timeout)

class handle_changes_thread(threading.Thread):

    def __init__(self, network):
        threading.Thread.__init__(self)
        self.network = network

    def run(self):
        self.network.handleChanges()


if __name__ == "__main__":
    sys.exit(main())
