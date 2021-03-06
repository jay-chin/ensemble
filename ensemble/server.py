#
# (c) 2015, Excelian Ltd
#

import sys
import logging
import time
import yaml
import os
import signal
import threading

from loader import (
        BasicSQLLoader,
        ConsumerDemandLoader,
        ConsumerResourceAllocationLoader,
        ResourceMetricsLoader,
        SessionHistoryLoader,
        SessionAttributesLoader
)
from helpers import get_db_engine, get_es_conn
import index_config.consumer_demand
import index_config.resource_metrics
import index_config.consumer_resource_allocation
import index_config.session_attributes
import index_config.session_history

CONFIG_FILE="config.yml" # always look for config.yml in CWD
cleanup_funcs = [] # List of cleanup functions to run before exiting
threads = [] # loader threads
kill_threads = False

def cleanup(*args):
    print("Cleaning up on exit")
    for f in cleanup_funcs:
        f()
    print("Finished cleaning up, exiting")
    sys.exit(0)

def worker(loader, interval):
    """ Continuously load data in between intervals """
    while not kill_threads:
        loader.load()
        time.sleep(interval)

def cleanup_threads():
    kill_threads = True
    print("Kill signal sent to threads")
    while threads:
        print("Number of threads alive : %d" % len(threads))
        print("Waiting 5 seconds for threads to finish")
        time.sleep(5)

def run(loaders, interval):
    """ Create a thread for each loader and start running

    :param loaders: list of loader objects 
    :param interval: duration in seconds between each loading iteration
    """ 
    for i in range(len(loaders)):
        t = threading.Thread(target=worker, args=(loaders[i], interval))
        threads.append(t)
        t.start()

def get_loaders(cfg, engine, es, logger):
    classmap = {
            "resource_metrics": ResourceMetricsLoader,
            "consumer_demand": ConsumerDemandLoader,
            "consumer_resource_allocation": ConsumerResourceAllocationLoader,
            "session_attributes": SessionAttributesLoader,
            "session_history": SessionHistoryLoader
    }
    loaders = []
    for loadername, loaderconf in cfg['loaders'].iteritems():
        config = getattr(index_config, loadername).config
        loaderclass = classmap.get(loadername, BasicSQLLoader)
        if loaderclass.__name__ == 'BasicSQLLoader':
            logger.warning("Couldn't find loader for %s, falling back to BasicSQLLoader" % loadername)
        else:
            logger.info("Created loader : %s" % loaderclass.__name__)
        loaders.append(loaderclass(db_engine=engine,
                            es_conn=es,
                            sql=loaderconf['sql'],
                            max_rows=cfg['setup']['max_rows'],
                            es_config=config))
    return loaders

def main():
    cfg = sys.argv[1] if len(sys.argv) > 1 else CONFIG_FILE
    if not os.path.isfile(cfg):
        sys.exit("%s does not exist or is not valid" % cfg)
    with open(cfg, 'r') as ymlfile:
        cfg = yaml.load(ymlfile)
    setup = cfg['setup']

    # Set up logging
    logger = logging.getLogger('Ensemble')
    logger.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] '
                                    '- [%(name)s] - %(message)s')
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    logger.info("Starting up with settings in %s" % CONFIG_FILE)

    # Get a DB connection
    for _ in range(setup['db_max_retries']):
        engine = get_db_engine(setup['db_host'], setup['db_port'],
                setup['db_name'], setup['db_user'], setup['db_pass'])
        if engine:
            logger.info("DB connection Initialised")
            break

        logger.warning("Retrying connection to Elasticsearch")
        time.sleep(setup['db_retry_wait'])
    else:
        logger.critical("Failed Connecting to Database")
        sys.exit(1)

    # Clean up DB connection on exit
    cleanup_funcs.append(engine.dispose)

    # Catch TERM and INT signals for cleanup  
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    # Get an Elasticsearch connection
    es_hosts = setup['es_hosts'].split(',')
    for _ in range(setup['es_max_retries']):
        es = get_es_conn(es_hosts, setup['es_user'], setup['es_user'], 
                setup['es_ssl'], setup['es_verify_certs'], setup['es_cacerts']) 
        if es:
            logger.info("Connected to Elasticsearch : %s" \
                % es.info().get('cluster_name', 'unknown'))
            break

        logger.warning("Retrying connection to Elasticsearch")
        time.sleep(setup['es_retry_wait'])
    else:
        logger.critical("Failed Connecting to Elasticsearch")
        sys.exit(1)

    # Build our list of SQL loaders
    logger.info("Building list of loaders")
    loaders = get_loaders(cfg, engine, es, logger) 

    # We got all our configs, let's run now
    logger.info("Initialisation complete: let's do some ETL !")
    run(loaders, setup['interval'])

if __name__ == '__main__':
    main()
