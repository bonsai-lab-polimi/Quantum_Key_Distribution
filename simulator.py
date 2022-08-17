import asyncio
import configparser
import logging
import os
import sys
import time
import traceback
from argparse import Namespace, ArgumentParser
from subprocess import Popen, PIPE
from random import randint, choice, shuffle
from datetime import datetime, timedelta

from httpx import AsyncClient


class Bcolors:
    """Unicode's characters to color the output."""
    MAGENTA = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def printProgressBar(iteration, total, prefix='', suffix='', decimals=1, length=100, fill=f'█', print_end="\r") -> None:
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    if iteration < total:
        fill = Bcolors.YELLOW + fill + Bcolors.ENDC
        percent = Bcolors.YELLOW + percent + "%" + Bcolors.ENDC
    else:
        fill = Bcolors.GREEN + fill + Bcolors.ENDC
        percent = Bcolors.GREEN + percent + "%" + Bcolors.ENDC
    bar = fill * filled_length + f'{Bcolors.BOLD}-{Bcolors.ENDC}' * (length - filled_length)
    print(f'\r{prefix} |{bar}| {percent} {suffix}', end=print_end)
    if iteration == total:
        print()


def get_cmd(entity: str) -> list[str]:
    global config
    return config[0]["SHARED"][entity].split(",")


class KME:
    def __init__(self, address: str, process: Popen) -> None:
        self.address = address
        self.process = process


class SAE:
    def __init__(self, address: str, ref_kme_addr: str) -> None:
        self.process = None
        self.cmd = get_cmd("sae")
        self.address = address
        self.ref_kme = ref_kme_addr

    def start(self) -> None:
        self.cmd += ["-k", self.ref_kme, "-a", self.address]
        self.process = Popen(self.cmd, stdin=PIPE, stdout=PIPE)


def stop() -> None:
    global qcs, sd_qkd_nodes, apps, controller
    time.sleep(2)
    logging.getLogger().warning("Shutdown qcs...")
    for qc in qcs:
        qc.terminate()
    time.sleep(2)

    logging.getLogger().warning("Shutdown kmes...")
    for node in sd_qkd_nodes:
        node.process.terminate()
    time.sleep(2)

    logging.getLogger().warning("Shutdown saes...")
    for app in apps:
        app.process.terminate()
    time.sleep(2)

    logging.getLogger().warning("Shutdown controller...")
    controller.terminate()

    logging.getLogger().warning("Exited.")
    print(f"{Bcolors.BOLD}\nSimulation finished, check log file for details.{Bcolors.ENDC}")
    time.sleep(2)
    sys.exit()


def set_sd_qkd_nodes() -> None:
    global sd_qkd_nodes
    print(f"{Bcolors.BOLD}\nSetting Key Manager Entities.{Bcolors.ENDC}")
    tot = int(get_config()["n_kme"])
    ports = get_config()["kme_ports"].split(",")
    for i in range(0, tot):
        sd_qkd_nodes.append(KME(address=f"127.0.0.1:{int(ports[0]) + i}", process=Popen(
            args=get_cmd("sd_qkd_node") + ["-a", f"127.0.0.1:{int(ports[0]) + i}", "-p", f"{int(ports[1]) + i}"],
            stdout=PIPE
        )))


def get_qcs() -> list[list[str]]:
    temp = get_config()["qcs"].split("/")
    res = []
    for qc in temp:
        res.append(qc.split(","))
    return res


def set_qcs() -> None:
    global qcs
    initial_params = get_cmd("qcs")
    shared_params = ["-i", f"5", "-lb", f"1000", "-ub", f"2000"]
    temp = get_qcs()
    print(f"{Bcolors.BOLD}\nSetting Quantum Channels.{Bcolors.ENDC}")
    for ports in temp:
        qcs.append(Popen(
            initial_params + ["-k1", f"127.0.0.1:{ports[0]}", "-k2", f"127.0.0.1:{ports[1]}"] + shared_params
        ))


def set_saes() -> None:
    global apps, sd_qkd_nodes
    print(f"{Bcolors.BOLD}\nSetting Secure Application Entities.{Bcolors.ENDC}")
    min_sae = int(get_config()["min_sae"])
    max_sae = int(get_config()["max_sae"])
    ports = [int(get_config()["sae_port"]), int(get_config()["kme_ports"].split(",")[0])]
    j = 0
    n = randint(min_sae, max_sae)
    logging.getLogger().warning(f"Starting {n} SAEs.\n")
    for i in range(0, n):
        sae = SAE(
            address=f"127.0.0.1:{ports[0] + i}",
            ref_kme_addr=f"127.0.0.1:{ports[1] + j if j < len(sd_qkd_nodes) else randint(ports[1], ports[1] + len(sd_qkd_nodes) - 1)}"
        )
        j += 1
        apps.append(sae)
        sae.start()


def create_random_connection(app_choices: dict[str, list[SAE]], addr: str) -> SAE:
    """Creates connections between two apps on different kmes."""
    key: str = choice(list(app_choices.keys()))
    while key == addr:
        key = choice(list(app_choices.keys()))
    b = choice(app_choices[key])
    return b


def set_connections() -> None:
    global connections, config, apps, sd_qkd_nodes
    key_length = config[0]["SHARED"]["key_length"].split(",")
    n_connections = randint(
        int(config[0]["SHARED"]["n_connections"].split(",")[0]),
        int(config[0]["SHARED"]["n_connections"].split(",")[1])
    )
    inter = config[0]["SHARED"]["request_interval"].split(",")
    choices_set = {}
    for node in sd_qkd_nodes:
        if node.address not in choices_set.keys():
            choices_set[node.address] = [app for app in apps if app.ref_kme == node.address]
    i = 0
    while i < n_connections:
        src = choice(apps)
        dst = create_random_connection(choices_set, src.ref_kme)
        ok = True
        for c in connections:
            # check if the connection already exists
            if c["src_port"] == src.address.split(":")[1] and c["target_port"] == dst.address.split(":")[1]:
                ok = False
                break
        if ok:
            # logging.getLogger().error(f"connection {i}: {src.address}, {dst.address}")
            connections.append({
                "src_ip": "127.0.0.1", "src_port": src.address.split(":")[1], "target_ip": "127.0.0.1",
                "target_port": dst.address.split(":")[1], "interval": randint(int(inter[0]), int(inter[1])),
                "key_length": key_length[randint(0, len(key_length) - 1)]
            })
            i += 1


async def start_connections() -> None:
    global connections, config
    new_conn_interval = int(config[0]["SHARED"]["new_connection_interval"])
    i_l = config[0]["SHARED"]["request_interval"].split(",")
    i = (int(i_l[0]) + int(i_l[1])) / 2
    async with AsyncClient() as client:
        shuffle(connections)
        tasks = []
        count = 0
        logging.getLogger().warning(f"Starting {len(connections)} connections.\n")
        for c in connections:
            async def request(conn, cnt):
                await asyncio.sleep(new_conn_interval * cnt)
                logging.getLogger().warning(f"{Bcolors.CYAN}Starting connection{Bcolors.ENDC}")
                await client.get(
                    url=f"http://{conn['src_ip']}:{conn['src_port']}/debugging_start_connection",
                    params={
                        "ip": conn['target_ip'], "port": int(conn['target_port']),
                        "interval": conn['interval'], "key_length": conn['key_length']
                    },
                    timeout=None
                )
            count += 1
            tasks.append(asyncio.ensure_future(request(c, count)))
        print(f"{Bcolors.BOLD}\nStarting {len(connections)} connections, "
              f"it takes at least {timedelta(seconds=len(connections) * new_conn_interval)} minutes...", end="\r")
        await asyncio.gather(*tasks)
        print(f"{Bcolors.BOLD}All connections started. Simulation is almost done, be patient.{Bcolors.ENDC}")


def reset_logs() -> None:
    open('logs.log', 'w').close()
    logging.basicConfig(level=logging.WARNING, filename="logs.log", format='\n%(asctime)s SIM: %(message)s')


def finished() -> None:
    global connections
    connections_with_errors = set()
    opened = 0
    connection_added = set()
    closed = 0
    connection_closed = set()
    logs = open('logs.log', 'r')
    connections_count = len(connections)
    failed = [0, 0]
    expired = 0
    tot = 0
    other_errors = 0
    keys_set: set[str] = set()
    keys_list: list[str] = []
    print(f"{Bcolors.BOLD}\nConnections terminated.{Bcolors.ENDC}")
    printProgressBar(0, connections_count, prefix='Progress:', suffix='Complete', length=50)
    end = False
    while not end:
        for line in logs.readlines():
            if f"{Bcolors.BLUE}KEY{Bcolors.ENDC} ->" in line:
                key = line.split(f"{Bcolors.BLUE}KEY{Bcolors.ENDC} -> ")[1].split(" [")[0]
                keys_set.add(key)
                keys_list.append(key)
            if f"Connection added:" in line:
                opened += 1
                connection_added.add(line.split("...")[1].split(" [")[0])
            if f"ERROR" in line:
                other_errors += 1
            if f"{Bcolors.MAGENTA}CRITICAL{Bcolors.ENDC}" in line:
                connections_with_errors.add(line.split("...")[1][:12])
            if "CTR: Connection closed:" in line:
                closed += 1
                connection_closed.add(line.split("...")[1])
            if "No path for the connection required" in line:
                failed[0] += 1
                other_errors -= 1
            if "Insufficient rate for the connection required" in line:
                failed[1] += 1
                other_errors -= 1
            if "Timeout connection." in line:
                other_errors -= 1
                expired += 1
            if opened == closed and (closed + failed[0] + failed[1] + expired == connections_count):
                end = True
            if tot < connections_count:
                tot = closed + failed[0] + failed[1] + expired
                printProgressBar(tot, connections_count, prefix='Progress:', suffix='Complete', length=50)
    logs.close()
    repeated = []
    for k in keys_set:
        count = 0
        for k1 in keys_list:
            if k[0:-2] in k1:
                count += 1
            if count > 2:
                repeated.append(k)
                break
        if len(repeated) > 0:
            break
    errors = len(connections_with_errors)
    success_perc = ((closed - errors) / connections_count) * 100
    expired_perc = (expired / connections_count) * 100
    no_path_perc = (failed[0] / connections_count) * 100
    insuff_rate_perc = (failed[1] / connections_count) * 100
    error_perc = (errors / connections_count) * 100
    other_errors_perc = (other_errors / connections_count) * 100
    print(f"\nTotal connections: {connections_count}"
          f"\nSuccessful connections: {'%.2f' % success_perc}%"
          f"\nConnections timeout expired: {'%.2f' % expired_perc}%"
          f"\nConnections with key exchange errors: {'%.2f' % error_perc}%"
          f"\n\tTotal generated keys: {len(keys_set)}"
          f"\n\tAll keys received by both parties: {len(keys_set) == len(keys_list) / 2}"
          f"\n\tReused bites in keys: {'True ' + str(repeated) if len(repeated) > 0 else 'False'}"
          f"\nConnections refused: {'%.2f' % (no_path_perc + insuff_rate_perc)}%"
          f"\n\tNo path for the connection required: {'%.2f' % no_path_perc}%"
          f"\n\tInsufficient rate for the connection required: {'%.2f' % insuff_rate_perc}%"
          f"\nOther errors: {'%.2f' % other_errors_perc}%")
    logging.getLogger().warning(f"\nTotal connections: {connections_count}"
                                f"\nSuccessful connections: {'%.2f' % success_perc}%"
                                f"\nConnections timeout expired: {'%.2f' % expired_perc}%"
                                f"\nConnections with key exchange errors: {'%.2f' % error_perc}%"
                                f"\n\tTotal generated keys: {len(keys_set)}"
                                f"\n\tAll keys received by both parties: {len(keys_set) == len(keys_list) / 2}"
                                f"\n\tReused bites in keys: {'True ' + str(repeated) if len(repeated) > 0 else 'False'}"
                                f"\nConnections refused: {'%.2f' % (no_path_perc + insuff_rate_perc)}%"
                                f"\n\tNo path for the connection required: {'%.2f' % no_path_perc}%"
                                f"\n\tInsufficient rate for the connection required: {'%.2f' % insuff_rate_perc}%"
                                f"\nOther errors: {'%.2f' % other_errors_perc}%")


def all_created(entities: list, check_str: str) -> None:
    count = 0
    logs = open('logs.log', 'r')
    printProgressBar(0, len(entities), prefix='Progress:', suffix='Complete', length=50)
    while count < len(entities):
        for line in logs.readlines():
            if check_str in line:
                count += 1
                printProgressBar(count, len(entities), prefix='Progress:', suffix='Complete', length=50)
    logs.close()


def start_network() -> None:
    global controller, sd_qkd_nodes, qcs, apps, connections
    reset_logs()

    try:
        print(f"{Bcolors.BOLD}Simulation started... Wait to finish.{Bcolors.ENDC}")
        controller = Popen(get_cmd("controller"), stdout=PIPE)
        time.sleep(2)  # To allow FastAPI to get started
        set_sd_qkd_nodes()
        all_created(entities=sd_qkd_nodes, check_str="CTR: KME added")
        set_qcs()
        all_created(entities=qcs, check_str="CTR: Link added:")
        set_saes()
        all_created(entities=apps, check_str="Started SAE at")
        set_connections()
        asyncio.run(start_connections())
    except Exception as e:
        logging.error(traceback.format_exc())


def set_config() -> tuple[str, configparser]:
    network: str = read_args().network
    temp = configparser.ConfigParser()
    temp.read(os.path.dirname(os.path.abspath(__file__)) + '/simulation.ini')
    return temp, network.upper()


def get_config() -> configparser:
    global config
    return config[0][config[1]]


def read_args() -> Namespace:
    """Read parameters from CLI."""
    parser = ArgumentParser(prog="poetry run python simulator.py")
    parser.add_argument(
        "-n",
        "--network",
        type=str,
        help="The network example from the 'simulation.ini' file. Default 'ring'.",
        default="ring",
    )

    return parser.parse_args()


config: tuple[configparser, str] = set_config()
controller: Popen
sd_qkd_nodes: list[KME] = []
qcs = []
apps: list[SAE] = []
connections = []

if __name__ == '__main__':
    start_network()
    new_conn_int = int(config[0]["SHARED"]["new_connection_interval"])
    interval_list = config[0]["SHARED"]["request_interval"].split(",")
    interval = (int(interval_list[0]) + int(interval_list[1])) / 2
    #time.sleep((len(connections) * new_conn_int + 5 * interval))
    finished()
    stop()
