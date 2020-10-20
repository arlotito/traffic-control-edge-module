# Copyright (c) arlotito. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for full license information.
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

import typing

import threading

import subprocess
from os import listdir
import re

import docker

BASE_PATH = "/trafficControl/sys/devices/virtual/net"

class TcWrapper():
    def __init__(self) -> None:
        
        # connect the docker host
        logger.info("connecting the docker host...")
        self.docker_client = docker.from_env()

        # Run a listener thread for docker events
        self.listen_thread = threading.Thread(target=self.dockerEventsListener)
        self.listen_thread.daemon = True
        self.listen_thread.start()

        # init to empty dictionary
        self.rules = {}

    # Listens to container lifecycle events via docker client.
    # In case a 'top' event is detected on a given container, 
    # it re-applies rules to that container.
    #
    # Parameters
    # ----------
    #
    # Returns
    # -------
    #
    # Raises
    # ------
    #
    def dockerEventsListener(self):
        for event in self.docker_client.events(decode=True):
            if 'status' in event:
                status = event['status']
                dockerEventTargetName = event['Actor']['Attributes']['name']
                logger.debug("Docker event: {}/{}".format(status, dockerEventTargetName))

                #'top' should be the last event after a create/start/restart cycle...
                if status=='top':
                    logger.debug("refreshing rules on module '{}'".format(dockerEventTargetName))
                    self.applyRules(self.rules, dockerEventTargetName)


    # Extracts the rules from the module twin (full or patch) and apply them.
    #
    # Parameters
    # ----------
    # twin : dict
    #   it's the full or patch twin, as received by the 'get_twin()' or 
    #   'receive_twin_desired_properties_patch()'
    #
    # Returns
    # -------
    #
    # Raises
    # ------
    #
    def extractRulesFromTwin(self, twin):

        if 'desired' in twin:
            # this is the FULL TWIN
            logger.debug("FULL TWIN received...")
            root = twin['desired'] #full
            self.rules = {} # clears the existing rules
        else:
            # this is a PATCH
            logger.debug("TWIN PATCH received...")
            root = twin #patch

        if 'rules' in root:
            logger.debug("------RECEIVED DOCUMENT--------")
            logger.debug(root['rules'])
            logger.debug("-------------------------------")

            for key in root['rules']:
                logger.debug("PATCH: key: {}, values: {}".format(key, root['rules'][key]))
                
                if root['rules'][key] != None:
                    self.rules[key] = root['rules'][key]
                else:
                    del self.rules[key]
                    del root['rules'][key]
            
            # displays updated rules list
            logger.debug("---UPDATED COMPLETE DOCUMENT---")
            logger.debug(self.rules)
            logger.debug("-------------------------------")
            
            # applies received rules (i.e. all if it was a full twin,
            # or only the ones that have been updated if it was a patch)
            self.applyRules(root['rules'], "any")

        else:
            logger.debug("no rules found in patch")

    # Evaluate and applies the rules 
    #
    # Parameters
    # ----------
    # rules : dict
    #   nested dict with the rules to be evaluated
    #   { {"<targetName>": {"targetType": "<targetType>", "rule": "<rule>"}, ...}
    # target : str
    #   will apply any rule (if target='any') or only the rules which targetName = target
    #
    # Returns
    # -------
    #
    # Raises
    # ------
    #
    def applyRules(self, rules, target: str):
        try:
            if len(rules) == 0:
                logger.debug("no rules to apply")
                return

            # loops through the rules list
            # rules = {"<targetName>": {"targetType": "<targetType>", "rule": "<rule>"}, ...}
            for targetName in rules:
                
                if target != "any" and target != targetName:
                    continue   #do not process this 

                logger.debug("Processing rule '{}'".format(targetName))

                # gets the target of the current tule
                targetType = rules[targetName]['targetType']

                if targetType == 'module':
                    # if target is a module, gets the VETH adapter attached to it...
                    veth = self._getVethName(targetName)
                
                elif targetType == 'if':
                    # if target is an interface name, it uses it as it is
                    veth = targetName
                
                else:
                    # target unknown
                    logger.error("'{}' is not a valid targetType".format(targetType))
                    return

                if veth != "":
                    # now that we have a valid VETH name, we apply the rule invoking tc
                    logger.debug("   {}".format(rules[targetName]))
                    logger.debug("   to {} with name '{}'".format(targetType, targetName))
                    
                    logger.debug("applying rule via 'tcset'")
                    self._invokeTcSet(veth, rules[targetName]['rule'])

                else:
                    # VETH name is invalid
                    logger.error("{} '{}' not found".format(targetType, targetName))
                    return

        except Exception as e:
            logger.error(e)

    # Retrieves the name of the virtual adapter (i.e. something like 'veth4ee5228') 
    # attached to the module/container 'moduleName' 
    #
    # Parameters
    # ----------
    # moduleName : str
    #   name of the module/container  
    #
    # Returns
    # -------
    # str
    #   this is the adapter name. For instance 'veth4ee5228'
    #
    # Raises
    # ------
    #
    def _getVethName(self, moduleName: str) -> str:

        # STEP 1: moduleName --> iflink
        # ------------------------------------------------
        # gets the 'iflink' of the target module/container
        # This is equivalent to:
        #       docker exec -it <moduleName> bash -c 'cat /sys/class/net/eth0/iflink'
        module = self.docker_client.containers.get(moduleName)

        cmd = '/bin/sh -c "cat /sys/class/net/eth0/iflink"'
        res = module.exec_run(cmd)

        iflink = res.output.decode("utf-8").rstrip()
        logger.debug("veth iflink: {}".format(iflink))


        # STEP 2: iflink --> virtual adapter name veth
        # ------------------------------------------------
        # gets the 'iflink' of the target module/container
        # This is equivalent to:
        #       grep -l <iflink> /trafficControl/sys/devices/virtual/net/veth*/ifindex
        files = listdir(BASE_PATH)
        veth = ""
        for fileName in files:
            if fileName.startswith("veth"):
                file = open("{}/{}/ifindex".format(BASE_PATH, fileName), "r")
                for line in file:
                    if re.search(str(iflink), line):
                        veth = fileName.strip()
                        logger.debug("veth found: {} (iflink='{}')".format(veth, iflink))
                        break

        return veth
    
    # This is the actual wrapper invoking the 'tcset' command-line tool
    #
    # Parameters
    # ----------
    # veth : str
    #   name of the virtual adapter the rule has to be applied to
    #   ex.: 'veth4ee5228'
    # 
    # args : str
    #   a string containing the rule to be applied
    #   ex.: '--overwrite --direction incoming --rate 27Kbps'
    #
    # Returns
    # -------
    #
    # Raises
    # ------
    #
    def _invokeTcSet(self, veth: str, args: str):
        #apply rule by calling 'tcset'
        cmd = ['tcset', veth] + args.split(" ")
        out = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout,stderr = out.communicate()
                    
        if stdout != None:
            logger.debug(stdout.decode('utf-8'))
        
        if stderr != None:
            logger.error(stderr.decode('utf-8'))

        #check result by calling 'tcshow' 
        cmd = ['tcshow', veth]
        out = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        stdout,stderr = out.communicate()

        if stdout != None:
            logger.debug(stdout.decode('utf-8'))
        
        if stderr != None:
            logger.error(stderr.decode('utf-8'))