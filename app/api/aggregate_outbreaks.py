"""
The functions in this file relate to aggregating cumulative and outbreak data for
a set of states (e.g. ME) that otherwise have multiple rows per date/facility, in a case of
several outbreaks.
"""

import flask
import numpy as np
import pandas as pd
from time import time

from app.api import utils


####################################################################################################
#######################################   FL-specific functions    #################################
####################################################################################################


def preclean_FL(df):
    # these aren't real data rows: dropping 
    df.drop(df[df.County.isin(['TOTAL ICF', 'TOTAL ALF', 'TOTALS'])].index, inplace = True)

    # some facilities have weird characters, replace as needed
    df['Facility'] = df['Facility'].str.replace('Ͳ','-')
    df['County'] = df['County'].str.replace('Ͳ','-')
    def process_county(county):
        if county in ['UNKNOWN', 'UNKNOWN COUNTY']:
            return ''
        elif county in ['DADE', 'MIAMI-DADE', 'MIAMIͲDADE', 'MIAMI‐DADE']:
            return 'MIAMI-DADE'
        else:
            return county
        
    df['County'] = df['County'].apply(process_county)


# cleans up CTP Facility Types and federal/state regulated
# this is optimized for FL - other states have different labels
def state_to_ctp_FL(record):
    state = record['State_Facility_Type']
    if(state == 'ALF' or state == 'Assisted Living'):
        record['CTP_Facility_Type'] = 'Assisted Living'
        record['Regulate'] = 'State'
    elif(state == 'NH'):
        record['CTP_Facility_Type'] = 'Nursing Home'
        record['Regulate'] = 'Federal'
    elif(state == 'ICF'):
        record['CTP_Facility_Type'] = 'Other'
        record['Regulate'] = 'State'
    else:
        record['CTP_Facility_Type'] = np.nan
        record['Regulate'] = np.nan
    return record


# clears any CMS IDs tied to facilities that are not nursing homes
# this is optimized for FL - other states have different labels
def clear_non_nh_cms_ids_FL(record):
    if ((record['State_Facility_Type'] != 'NH') and (not pd.isnull(record['CMS_ID']))):
        record['CMS_ID'] = np.nan
    return record


def postclean_FL(df):
    df = df.apply(state_to_ctp_FL, axis = 1)
    df = df.apply(clear_non_nh_cms_ids_FL, axis = 1)
    return df


####################################################################################################
#######################################   Aggregation logic    #####################################
####################################################################################################


# takes a dataframe containing the same facility name/date data and collapses the rows.
# Finds conceptually paired columns based on the content of col_map.
def collapse_rows_new_header_names(df_group, col_map):
    if df_group.shape[0] == 1:
        return df_group

    new_df_subset = df_group.loc[df_group['Outbrk_Status'] == 'OPEN'].copy()
    row_descriptor = '%s %s %s %s' % (
        set(new_df_subset['Facility']),
        set(new_df_subset['State_Facility_Type']),
        set(new_df_subset['County']), 
        set(new_df_subset['Date']))

    # expecting only one row/open outbreak; if this isn't true, check that the columns are the same
    if new_df_subset.shape[0] > 1:
        deduped = new_df_subset.drop_duplicates()
        if deduped.shape[0] > 1:
            flask.current_app.logger.info(
                'Multiple open outbreak rows with different data: %s' % row_descriptor)
            return df_group
        else:
            # still duplicate rows, but these are the same data so we can trust it
            flask.current_app.logger.info('Duplicate open outbreak rows: %s' % row_descriptor)
            new_df_subset = deduped

    if new_df_subset.empty:  # no open outbreaks, but we may want to merge some closed ones
        new_df_subset = df_group.head(1)
        
    for colname in col_map.keys():
        try:
            cumulative_val = df_group[colname].fillna(0).astype(int).sum()
            current_open_val = df_group[col_map[colname]].fillna(0).astype(int).sum()
            val = cumulative_val + current_open_val
            if val > 0:
                index = list(df_group.columns).index(colname)
                new_df_subset.iat[0,index] = val
        except ValueError:  # some date fields in numeric places, return group without collapsing
            flask.current_app.logger.info(
                'Some non-numeric fields in numeric places, column %s: %s' % (
                    colname, row_descriptor))
            return df_group

    return new_df_subset


def collapse_outbreak_rows(df):
    col_map = utils.make_matching_column_name_map(df)
    # group by facility name and date, collapse each group into one row
    processed_df = df.groupby(
        ['Date', 'Facility', 'County', 'State_Facility_Type'], as_index=False).apply(
        lambda x: collapse_rows_new_header_names(x, col_map))

    processed_df.sort_values(
        by=['Date', 'County', 'City', 'Facility'], inplace=True, ignore_index=True)
    return processed_df


def do_aggregate_outbreaks(df):
    flask.current_app.logger.info('DataFrame loaded: %d rows' % df.shape[0])

    utils.standardize_data(df)

    # TODO: if more than FL needs special treatment before aggregating outbreaks, factor this out
    # into something nicer
    state = set(df['State']).pop()
    if state == 'FL':
        preclean_FL(df)

    t1 = time()
    processed_df = collapse_outbreak_rows(df)
    t2 = time()

    if state == 'FL':
        processed_df = postclean_FL(processed_df)

    # this will go into the lambda logs
    flask.current_app.logger.info('Collapsing %s data took %.1f seconds, %d to %d rows' % (
        state, (t2 - t1), df.shape[0], processed_df.shape[0]))

    return processed_df


def cli_aggregate_outbreaks(outfile, url):
    utils.cli_for_function(do_aggregate_outbreaks, outfile, url)