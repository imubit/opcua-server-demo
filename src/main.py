#!/usr/bin/env python

import asyncio
import logging
import os

from asyncua import ua, Server
from tcx_handler import TCXHandler

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(name)-20s  - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


class MirrorHandler(object):

    def __init__(self, server, orig_data, copy_data):
        self.server = server
        self.orig_data = orig_data
        self.copy_data = copy_data
        self.sub = None

    async def start(self):
        self.sub = await self.server.create_subscription(500, self)
        await self.orig_data.set_writable()
        await self.sub.subscribe_data_change(self.orig_data)

    async def datachange_notification(self, node, val, data):
        if self.orig_data == node:
            if not isinstance(val, bool):
                logger.error("Node value is not boolean")
                return
            await self.server.write_attribute_value(self.copy_data.nodeid, ua.DataValue(val))


class TCXUpdateHandler(object):

    def __init__(self, filepath, lat_data, long_data, lat_long_data):
        self.filepath = filepath
        self.lat_data = lat_data
        self.long_data = long_data
        self.lat_long_data = lat_long_data
        self.tcx = TCXHandler(filepath)

    async def on_update(self, position):
        lat = position.get("latitude")
        long = position.get("longitude")
        lat_long = "lat={},long={}".format(lat, long)
        if lat: await self.lat_data.write_value(lat)
        if long: await self.long_data.write_value(long)
        if long and lat: await self.lat_long_data.write_value(lat_long)

    async def start(self):
        self.tcx.register_callback(self.on_update)
        await self.tcx.start()


async def toggle_data(data, refresh=1, init=False):
    value = init
    while True:
        await data.write_value(value)
        await asyncio.sleep(refresh)
        value = not value


async def periodic_data(data, refresh=1, init=0, increment=1):
    value = init
    while True:
        await data.write_value(value)
        await asyncio.sleep(refresh)
        value += increment


async def random_data(data, refresh=1, init=0, min=0, max=10):
    import random
    value = init
    while True:
        await data.write_value(value)
        await asyncio.sleep(refresh)
        value = round(random.uniform(min, max), 2)


async def cyclic_data(data, cycle_time=1, step=0.1, init=0, min=0, max=100):
    value = init
    interval = max - min
    spi = interval / step # steps per interval
    refresh = cycle_time / spi
    increasing = True
    while True:
        await data.write_value(ua.Double(value))
        await asyncio.sleep(refresh)
        if value >= max:
            increasing = False
        if value <= min:
            increasing = True
        value = value + step if increasing else value - step


async def main():
    # Certificates folder
    certs_folder = os.environ.get('CERTS_FOLDER', os.path.dirname(os.path.realpath(__file__)))
    key_pem_path = os.path.join(certs_folder, "key.pem")
    cert_der_path = os.path.join(certs_folder, "certificate.der")

    server = Server()
    await server.init()
    await server.set_application_uri("urn:opcua:iom:server")
    server.set_endpoint('opc.tcp://0.0.0.0:4840/freeopcua/server/')
    server.set_server_name("IoM PLC Server Example")

    # Security
    await server.load_certificate(cert_der_path)
    await server.load_private_key(key_pem_path)
    server.set_security_policy([
                ua.SecurityPolicyType.NoSecurity,
                ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])

    # setup our own namespace, not really necessary but should as spec
    idx = await server.register_namespace("https://tknika.eus/opcua/demo/plc")
    # get Objects node, this is where we should put our nodes
    objects = server.get_objects_node()
    # populating our address space
    plc_server = await objects.add_object(idx, 'PLC Server')

    bool_data = await plc_server.add_variable(idx, 'BooleanData', True, datatype=ua.NodeId(ua.ObjectIds.Boolean, 0))
    pos_data = await plc_server.add_variable(idx, 'PositiveTrendData', 0, datatype=ua.NodeId(ua.ObjectIds.Double, 0))
    neg_data = await plc_server.add_variable(idx, 'NegativeTrendData', 0, datatype=ua.NodeId(ua.ObjectIds.Double, 0))
    temp_data = await plc_server.add_variable(idx, 'TemperatureData', 18.5, datatype=ua.NodeId(ua.ObjectIds.Double, 0))
    hum_data = await plc_server.add_variable(idx, 'HumidityData', 60.2, datatype=ua.NodeId(ua.ObjectIds.Double, 0))
    cyc_data = await plc_server.add_variable(idx, 'CyclicData', 0.0, datatype=ua.NodeId(ua.ObjectIds.Double, 0))
    mirror_orig_data = await plc_server.add_variable(idx, 'MirrorDataOriginal', True, datatype=ua.NodeId(ua.ObjectIds.Boolean, 0))
    mirror_copy_data = await plc_server.add_variable(idx, 'MirrorDataCopy', True, datatype=ua.NodeId(ua.ObjectIds.Boolean, 0))
    latitude_data = await plc_server.add_variable(idx, "GPSLatitude", "", datatype=ua.NodeId(ua.ObjectIds.String, 0))
    longitude_data = await plc_server.add_variable(idx, "GPSLongitude", "", datatype=ua.NodeId(ua.ObjectIds.String, 0))
    latitude_longitude_data = await plc_server.add_variable(idx, "GPSLatitudeAndLongitude", "", datatype=ua.NodeId(ua.ObjectIds.String, 0))

    # enable historization
    await server.historize_node_data_change(cyc_data, period=None, count=10000)

    logger.info('Starting OPC UA server!')

    bool_task = asyncio.Task(toggle_data(bool_data, refresh=10, init=True))
    pos_task = asyncio.Task(periodic_data(pos_data, refresh=5))
    neg_task = asyncio.Task(periodic_data(neg_data, refresh=5, increment=-2))
    temp_task = asyncio.Task(random_data(temp_data, refresh=10, init=18.5, min=15, max=22))
    hum_task = asyncio.Task(random_data(hum_data, refresh=10, init=60.2, min=0, max=100))
    cyclic_task = asyncio.Task(cyclic_data(cyc_data, cycle_time=200, step=0.5, init=0, min=-100, max=100))
    
    mirror_handler = MirrorHandler(server, mirror_orig_data, mirror_copy_data)
    await mirror_handler.start()

    tcx_update_handler = TCXUpdateHandler("circular-urnieta-aia-donosti-urnieta.tcx", latitude_data, longitude_data, latitude_longitude_data)
    tcx_task = asyncio.Task(tcx_update_handler.start())

    async with server:
        await asyncio.gather(bool_task, pos_task, neg_task, temp_task, hum_task, cyclic_task, tcx_task)


if __name__ == '__main__':
    logger.info('Starting application')
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.close()