# Copyright (c) arlotito. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for full license information.
import logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s.%(msecs)03d - [%(levelname)s] - [%(funcName)s] %(message)s', datefmt='%d-%b-%y %H:%M:%S')
logger = logging.getLogger(__name__)
logger.setLevel(level=logging.INFO)

import threading
from azure.iot.device import IoTHubModuleClient
import time

# this is the class with the actual tc wrapper
from tc.wrapper import TcWrapper

# Gets and decodes a twin patch. Extracts the rule (if any) and applies.
def twin_patch_listener(module_client, tc_wrapper: TcWrapper):
    while True:
        patch = module_client.receive_twin_desired_properties_patch()
        tcWrapper.extractRulesFromTwin(patch)
        
if __name__ == "__main__": 
    
    # connect the iot edge module client.
    logger.info("connecting the iot edge module client...")
    module_client = IoTHubModuleClient.create_from_edge_environment()
    module_client.connect()

    # instantiates the TC wrapper
    tcWrapper = TcWrapper()

    # Run a Module Twin listener thread in the background
    listen_thread = threading.Thread(target=twin_patch_listener, args=(module_client, tcWrapper))
    listen_thread.daemon = True
    listen_thread.start()

    # gets the twin and applies the rules (if any)
    twin = module_client.get_twin()
    tcWrapper.extractRulesFromTwin(twin)

    # loops forever
    while True:
        time.sleep(0.1)
