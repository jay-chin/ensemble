#
# (c) 2015, Excelian Ltd
#
# file: ensemble/server.py
# description : server ETL process  
# ============================================================================

import sys
import logging
import sched
import time
import yaml
import os

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

def run(loaders, interval, logger):
    """ Main loop of server ETL that continuously schedules each loader

    :param loaders: list of loader objects 
    :param interval: duration in seconds between each loading iteration
    :param logger: logger object
    """ 
    while True:
        logger.info('Begin scheduling tasks')
        scheduler = sched.scheduler(time.time, time.sleep)
        
        # Add tasks to scheduler
        for loader in loaders:
            loader.load()
            scheduler.enter(1, 1, loader.load, ())

        # Run all tasks after adding them
        scheduler.run()
        logger.info('All tasks completed, sleep %d seconds' % interval)
        time.sleep(interval)  # Sleep before next scheduling interval
    return

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
        logger.info("Creating loader : %s" % loadername)
        config = getattr(index_config, loadername).config
        loaderclass = classmap.get('loadername', BasicSQLLoader)
        loaders.append(loaderclass(db_engine=engine,
                            es_conn=es,
                            sql=loaderconf['sql'],
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
    engine = get_db_engine(setup['db_host'], setup['db_port'],
            setup['db_name'], setup['db_user'], setup['db_pass'])
    if not engine:
        sys.exit("Invalid configuration for DB")

    logger.info("DB Engine done")
    # Get an Elasticsearch connection
    es_hosts = setup['es_hosts'].split(',')
    for _ in range(setup['es_max_retries']):
        es = get_es_conn(es_hosts, setup['es_ssl'],
                    setup['es_verify_certs'], setup['es_cacerts']) 
        if es:
            logger.info("Connected to Elasticsearch : %s" \
                % es.info().get('cluster_name', 'unknown'))
            break

        logger.warning("Retrying connection to Elasticsearch")
        time.sleep(setup['es_retry_wait'])
    else:
        logger.criticial("Failed Connecting to Elasticsearch")
        sys.exit(1)

    # Build our list of SQL loaders
    logger.info("Building list of loaders")
    loaders = get_loaders(cfg, engine, es, logger) 

    # We got all our configs, let's run now
    logger.info("Initialisation complete: let's do some ETL !")
    run(loaders, setup['interval'], logger)

if __name__ == '__main__':
    main()
