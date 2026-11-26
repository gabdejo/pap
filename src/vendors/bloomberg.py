# src/vendors/bloomberg.py
# ---------------------------------------------------------------
# Bloomberg blpapi client helpers: session management and BDP/reference-
# data request wrappers, returning pandas DataFrames.

import blpapi
import pandas as pd
from typing import Iterable, Dict, Any
import itertools
import logging

logger = logging.getLogger(__name__)

def start_session(session = blpapi.Session()):

    if not session.start():
        raise ValueError("Failed to start session.")

    return session

def get_securityData(toPy_list, subservice):
    """
    Pulls the security data from a .toPy (response.toPy()['securityData'])
    
    :param toPy_list: response.toPy() list
    :param subservice: Type of request (Historical or Reference)

    returns a list in which each element is a dict corresponding to a security
    a.k.a. response.toPy()['securityData']
    """
    securityData = map(lambda x:x['securityData'], toPy_list)

    if subservice == 'HistoricalDataRequest':
        return [*securityData]
    elif subservice == 'ReferenceDataRequest':
        #Aditional step: flatten the list
        return [*itertools.chain.from_iterable(securityData)]

def secdata_to_df(sec_data):
    """
    :param sec_data: response.toPy()['securityData'] or equivalent
    so input is a dict in shape {'security':id, 'eidData':[], ..., 'fieldaData':[{}]}
    """
    if type(sec_data['fieldData']) is list:
        ind = None
    else:
        ind = [0]

    df = pd.DataFrame(sec_data['fieldData'], index=ind)\
    .assign(id = sec_data['security'])
    return df

def secdata_list_to_df(sec_data_list):
    df = pd.concat(map(secdata_to_df, sec_data_list)).reset_index(drop=True)
    return df

def bulk_to_df(toPy_list, field):
    """
    Parses a BULK reference field (one that returns a table per security,
    e.g. EQY_DVD_ADJUST_FACT) into a long DataFrame: one row per bulk row,
    columns = 'id' + the bulk's own sub-fields as Bloomberg names them.

    secdata_to_df cannot do this: it puts the whole table in one cell, so
    its output shape depends on the row count (0 rows -> id only, 1 row ->
    a dict in a cell, 2+ rows -> ValueError). Here a security with no rows
    simply contributes no rows, and an empty result is an empty DataFrame.

    :param toPy_list: response.toPy() list of a ReferenceDataRequest
    :param field: the bulk field requested
    """
    rows = []
    for sec in get_securityData(toPy_list, 'ReferenceDataRequest'):
        if sec.get('securityError'):
            logger.warning(f"{sec['security']}: securityError {sec['securityError']}")
        if sec.get('fieldExceptions'):
            logger.warning(f"{sec['security']}: fieldExceptions {sec['fieldExceptions']}")
        for row in sec.get('fieldData', {}).get(field, []):
            rows.append({'id': sec['security'], **row})
    return pd.DataFrame(rows)

def create_request(session, subservice):
    
    SERVICE = "//blp/refdata"
    SUB_SERVICE = subservice
    
    if not session.start():
        raise ValueError("Failed to start session.")

    if not session.openService(SERVICE):
        raise ValueError(f"Failed to open {SERVICE}")
    
    service = session.getService(SERVICE)
    request = service.createRequest(SUB_SERVICE)

    return request

def data_request(
    securities: Iterable[str],
    fields: Iterable[str],
    start_date: str | None = None,
    end_date: str | None = None,
    options: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """
    Generate named arguments dictionary of historical data Bloomberg API "a la BDH"
    
    :param securities: Iterable of tickers parsekeyables or other IDs (ISIN, CUSIP, etc)
    :type securities: Iterable[str]
    :param fields: FLDS fields
    :type fields: Iterable[str]
    :param start_date: Start date string
    :type start_date: str | None
    :param end_date: End date string
    :type end_date: str | None
    :param options: Optional arguments / overrides
    :type options: Dict[str, Any] | None
    :return: Named arguments dictionary 
    :rtype: Dict[str, Any]
    """

    # Raise error if missing mandatory parameters
    if securities is None:
        raise ValueError('securities is required')
    if fields is None:
        raise ValueError('fields is required')
    
    # Handle atomic strings, convert to len=1 list
    if type(securities) is str:
        securities = [securities]
    if type(fields) is str:
        fields = [fields]

    return {
        'securities': list(securities),
        'fields': list(fields),
        'start_date': start_date,
        'end_date': end_date,
        'options': options or {}
    }

def fetch_request(session, subservice, options, parse=True):
    """parse=False returns the response.toPy() list instead of a DataFrame."""

    try:
        request = create_request(session=session, subservice=subservice)
    except ValueError as e:
        print(f'Error {e}')
        return

    # Mandatory parameters
    SECURITIES = blpapi.Name("securities")
    FIELDS = blpapi.Name("fields")    

    # Optional parameters (depends if historical or reference)
    START_DATE = blpapi.Name("startDate")
    END_DATE = blpapi.Name("endDate")

    # NAMES = {
    #     'currency': blpapi.Name('currency'),
    #     'periodicitySelection': blpapi.Name("periodicitySelection"),
    #     'pricingOption': blpapi.Name('pricingOption')
    # }

    # Add securities to request
    request[SECURITIES] = options['securities']

    # Add fields to request
    request[FIELDS] = options['fields']

    # Set start and end date
    if options['start_date'] is not None:
        request[START_DATE] = options['start_date'].replace('-','')

    if options['end_date'] is not None:
        request[END_DATE] = options['end_date'].replace('-','')

    # Set optional parameters
    for key, value in options['options'].items():
        request[blpapi.Name(key)] = value

    session.sendRequest(request)

    # responses are event elements appended, defined as messages (msg) by this module 
    responses = []

    done = True
    while done:
        event = session.nextEvent()
        for msg in event:
            if msg.messageType() == blpapi.Names.REQUEST_FAILURE:
                print("Request failed! returning raw messages")
                done = False
                return responses #break
        if event.eventType() == blpapi.Event.PARTIAL_RESPONSE:
            responses.append(msg)
        elif event.eventType() == blpapi.Event.RESPONSE:
            responses.append(msg)
            break
    
    toPys = [*map(lambda x:x.toPy(), responses)]

    if not parse:
        return toPys

    try:
        df = secdata_list_to_df(get_securityData(toPys, subservice=subservice))
    except ValueError as e:
        print(f'Response parsing error: {e}')
        return toPys

    return df

def fetch_historical_request(options):
    session = blpapi.Session()
    return fetch_request(session, 'HistoricalDataRequest', options)

def fetch_reference_request(options):
    session = blpapi.Session()
    return fetch_request(session, 'ReferenceDataRequest', options)

def fetch_bulk_request(options):
    """
    ReferenceDataRequest for ONE bulk field, parsed by bulk_to_df.
    Raises when Bloomberg returned no message at all, or when a requested
    security is absent from the response, so a failed or truncated request
    is never mistaken for "this security has no rows".
    """
    field, = options['fields']
    session = blpapi.Session()
    toPys = fetch_request(session, 'ReferenceDataRequest', options, parse=False)
    if not toPys:
        raise RuntimeError(f'Bloomberg returned no response for {field}')
    answered = {s['security'] for s in get_securityData(toPys, 'ReferenceDataRequest')}
    missing = sorted(set(options['securities']) - answered)
    if missing:
        raise RuntimeError(
            f'{field}: {len(missing)} of {len(options["securities"])} securities '
            f'missing from the response, e.g. {missing[:5]}'
        )
    return bulk_to_df(toPys, field)
