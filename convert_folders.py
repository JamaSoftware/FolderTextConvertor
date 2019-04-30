import sys
import json
import datetime
import time as time
import configparser
from progress.bar import ChargingBar
from py_jama_rest_client.client import JamaClient

# make the client and config globally available
global config
global client

# global variables these reset after each set root item is processed
global folder_item_type
global text_item_type
global set_item_type
global item_type_map
global item_id_to_child_map
global item_id_to_item_map
global pick_list_option_map
global item_count
global items_list

# stats for nerds (don't reset these values)
global conversion_count
global moved_item_count
conversion_count = 0
moved_item_count = 0


def reset_globals():
    global folder_item_type
    global text_item_type
    global set_item_type
    global item_type_map
    global item_id_to_child_map
    global item_id_to_item_map
    global pick_list_option_map
    global item_count
    global items_list
    folder_item_type = None
    text_item_type = None
    set_item_type = None
    item_type_map = {}
    item_id_to_child_map = {}
    item_id_to_item_map = {}
    pick_list_option_map = {}
    item_count = 0
    items_list = []


def init_jama_client():
    instance_url = str(config['CREDENTIALS']['instance url'])
    using_oauth = config['CREDENTIALS']['using oauth'] == 'True'
    username = str(config['CREDENTIALS']['username'])
    password = str(config['CREDENTIALS']['password'])
    return JamaClient(instance_url, credentials=(username, password), oauth=using_oauth)


def validate_parameters():
    set_ids_string = config['PARAMETERS']['set item ids']
    api_field_name = config['PARAMETERS']['api field name']
    field_value = config['PARAMETERS']['field value']

    if set_ids_string is None or set_ids_string == '':
        print("ERROR: a value for the 'set item ids' parameter in config file must be provided")
        return False
    if api_field_name is None or api_field_name == '':
        print("ERROR: a value for the 'api field name' parameter in config file must be provided")
        return False
    if field_value is None or field_value == '':
        print("ERROR: a value for the 'field value' parameter in config file must be provided")
        return False

    return True


def get_preserve_order():
    preserve_order = config['OPTIONS']['preserve order'].lower()
    if preserve_order == 'false' or preserve_order == 'no':
        return False
    else:
        return True


def get_stats_for_nerds():
    stats_for_nerds = config['OPTIONS']['stats for nerds'].lower()
    if stats_for_nerds == 'false' or stats_for_nerds == 'no':
        return False
    else:
        return True


def get_create_snapshot():
    create_snapshot = config['OPTIONS']['create snapshot'].lower()
    if create_snapshot == 'false' or create_snapshot == 'no':
        return False
    else:
        return True


def get_set_ids():
    set_ids_string = config['PARAMETERS']['set item ids']
    split_ids = set_ids_string.split(',')
    return_list = []
    for split_id in split_ids:
        return_list.append(int(split_id.strip()))
    return return_list


def validate_config():
    # both credentials and parameters are required
    credentials = ['instance url', 'using oauth', 'username', 'password']
    parameters = ['set item ids', 'api field name', 'field value']
    # these are optional
    options = ['preserve order', 'stats for nerds', 'create snapshot']

    # lets run some quick validations here
    for credential in credentials:
        if credential not in config['CREDENTIALS']:
            print("Config missing required credential '" + credential
                  + "', confirm this is present in the config.ini file.")
            return False
    for parameter in parameters:
        if parameter not in config['PARAMETERS']:
            print("Config missing required parameter '" + parameter
                  + "', confirm this is present in the config.ini file.")
            return False

    return True


# lets validate the user credentials
def validate_user_credentials(client):
    response = client.get_server_response()
    status_code = response.status_code
    if status_code != 200:
        return False
    # if we have made it this far then were good
    return True


# this script will only work if the root set its are actually of type set
def validate_set_item_ids(item_ids):
    for item_id in item_ids:
        current_item = client.get_item(item_id)
        if current_item.get('itemType') != set_item_type.get('id'):
            return False
    return True


# get at that instance meta data
def get_meta_data():
    global folder_item_type
    global text_item_type
    global set_item_type
    global item_type_map
    global pick_list_option_map

    # lets collect all the instance meta data were going to need before we run the conversions
    item_types = client.get_item_types()
    for item_type in item_types:
        # grab the type key, this *should* be consistent across Jama connect instances
        type_key = item_type.get('typeKey')
        item_type_id = item_type.get('id')
        if type_key == 'FLD':
            folder_item_type = item_type
        if type_key == 'TXT':
            text_item_type = item_type
        if type_key == 'SET':
            set_item_type = item_type

        item_type_map[item_type_id] = item_type


# helper method to determine if this is an item that we are going to convert
def is_conversion_item(fields, item_type_id):
    # is this already a folder? no work needed here then
    if item_type_id == folder_item_type.get('id'):
        return False

    field_definitions = item_type_map[item_type_id].get('fields')
    # match on the api field name
    key = None
    value = None

    api_field_name = str(config['PARAMETERS']['api field name'])
    field_value = str(config['PARAMETERS']['field value'])

    # determine what key were working with here. custom fields will be fieldName $ itemTypeID
    if api_field_name in fields:
        key = api_field_name
    elif api_field_name + '$' + str(item_type_id) in fields:
        key = api_field_name + '$' + str(item_type_id)
    else:
        return False

    # grab the field value
    value = fields.get(key)

    # iterate over all the field definitions here to find a match on the field were working with here
    # the point of doing this is to determine the field type. if look up -> do more work.
    for field_definition in field_definitions:
        field_definitions_name = field_definition.get('name')
        # found it!, lets look and see what were working with here.
        if field_definitions_name == key:
            # is this a lookup of type picklist?
            if field_definition.get('fieldType') == 'LOOKUP' and 'pickList' in field_definition:

                # we have a match on the id?
                if field_value == value:
                    return True

                # dive deeper, grab the picklist option here.
                pick_list_option = get_pick_list_option(value)
                return field_value == pick_list_option.get("name")
            # else lets just assume this is a string were matching up.
            else:
                return field_value == value


def get_pick_list_option(pick_list_option_id):
    # let make sure to only do this work once.
    if pick_list_option_id in pick_list_option_map:
        return pick_list_option_map.get(pick_list_option_id)
    else:
        pick_list_option = client.get_pick_list_option(pick_list_option_id)
        pick_list_option_map[pick_list_option_id] = pick_list_option
        return pick_list_option


def process_children_items(root_item_id, temp_folder_id, child_item_type, bar):
    # children_items = client.get_children_items(root_item_id)
    global moved_item_count, conversion_count
    children_items = item_id_to_child_map.get(root_item_id)

    # lets first do a quick pass to see if we need to process these children items
    conversions_detected = False
    for child_item in children_items:
        if is_conversion_item(child_item.get('fields'), child_item.get('itemType')):
            conversions_detected = True
            break

    # process all the children
    for child_item in children_items:
        item_type_id = child_item.get('itemType')
        fields = child_item.get('fields')
        item_id = child_item.get('id')

        # can we skip the work here?
        if conversions_detected:
            # we got a match on the value? lets "convert" it
            if is_conversion_item(fields, item_type_id):
                folder_id = convert_item(child_item, child_item_type, root_item_id)
                item_id_to_child_map[folder_id] = item_id_to_child_map.get(item_id)
                item_id = folder_id
                conversion_count += 1

            # no? well we still need to do work here to maintain order
            else:
                #  unless we don't care about order?
                if get_preserve_order():
                    move_item_to_parent_location(item_id, temp_folder_id)
                    move_item_to_parent_location(item_id, root_item_id)
                    moved_item_count += 1

        # lets check for sub children here and recursively call this if there are
        process_children_items(item_id, temp_folder_id, child_item_type, bar)
        bar.next()


# this is the "convert" (those are dramatic air quotes) here is how we are going to convert this:
#   1. create a folder item with the same parent
#   2. if there are children then move those over to the new folder item too.
#   3. delete the original item
def convert_item(item, child_item_type, parent_item_type_id):
    item_id = item.get("id")
    folder_id = create_folder(item, child_item_type, parent_item_type_id)
    children = client.get_children_items(item_id)
    # we will need to iterate over all the children here, and move them to the new folder
    for child in children:
        child_item_id = child.get("id")
        move_item_to_parent_location(child_item_id, folder_id)
    # there should be zero children in the original item now.
    client.delete_item(item_id)
    return folder_id


#
# def init_progress_bar():


# recursively gets all the items and assigns them to a map, also gets the count
def retrieve_items(root_item_id):
    global item_count
    global items_list
    children = client.get_children_items(root_item_id)
    items_list += children
    item_count += len(children)
    item_id_to_child_map[root_item_id] = children
    # we need to get all the children items too.
    for child in children:
        child_id = child.get('id')
        retrieve_items(child_id)


# the point of this method is to filter out the read only fields its okay to have extra fields
# that dont map, but the read only ones will cause the API to throw errors.
def get_fields_payload(fields):
    #  we have already pulled down all the item type meta data, lets use it here
    global item_type_map
    item_type_definition = folder_item_type
    item_type_fields = item_type_definition.get('fields')

    # we will be sending back a payload all ready for the API to consume
    payload = {}

    # loop through each field from the passed in fields object
    for field_name, field_value in fields.items():
        read_only = True

        # loop through the corresponding item type def fields.
        for item_type_field in item_type_fields:
            # match by the field api name
            if item_type_field.get('name') == field_name:
                read_only = item_type_field.get('readOnly')
                break
        if not read_only:
            payload[field_name] = field_value

    return payload


# create a temp folder soo we can re-order the items. (API does not allow you to change the order)
def create_folder(item, child_item_type, parent_item_id):
    fields = item.get('fields')
    fields = get_fields_payload(fields)
    project = item.get('project')
    folder_item_type_id = folder_item_type.get('id')
    location = {'item': parent_item_id}
    response = client.post_item(project, folder_item_type_id, child_item_type, location, fields)
    return response


# create a temp folder soo we can re-order the items. (API does not allow you to change the order)
def create_temp_folder(root_set_item_id, child_item_type_id):
    item = client.get_item(root_set_item_id)
    project = item.get('project')
    folder_item_type_id = folder_item_type.get('id')
    location = {'item': set_item_id}
    fields = {"name": "TEMP"}
    response = client.post_item(project, folder_item_type_id, child_item_type_id, location, fields)
    return response


def move_item_to_parent_location(item_id, destination_parent_id):
    if item_id == destination_parent_id:
        return
    payload = [
        {
            "op": "replace",
            "path": "/location/parent",
            "value": destination_parent_id
        }
    ]
    client.patch_item(item_id, payload)


def get_child_item_type(item_id):
    item = client.get_item(item_id)
    return item.get("childItemType")


def create_snapshot(set_id):
    ts = time.time()
    time_stamp = datetime.datetime.fromtimestamp(ts).strftime('%d-%m-%Y_%H-%M-%S')
    file_name = 'backup_set_ID-' + str(set_id) + '___' + str(time_stamp) + '.json'
    with open(file_name, 'w') as outfile:
        json.dump(items_list, outfile)


if __name__ == '__main__':
    global config
    global client
    start = time.time()
    reset_globals()
    print('\n'
          + '     ____     __   __          _____                      __           \n'
          + '    / __/__  / /__/ /__ ____  / ___/__  ___ _  _____ ____/ /____  ____ \n'
          + '   / _// _ \/ / _  / -_) __/ / /__/ _ \/ _ \ |/ / -_) __/ __/ _ \/ __/ \n'
          + '  /_/  \___/_/\_,_/\__/_/    \___/\___/_//_/___/\__/_/  \__/\___/_/    \n'
          + '                               Jama Software - Professional Services   \n'
          )

    config = configparser.ConfigParser()
    config.read('config.ini')

    # read in the configuration, will abort script if missing requried params
    if not validate_config():
        sys.exit()

    client = init_jama_client()

    # validate user data
    if not validate_user_credentials(client):
        print('Invalid username and/or password, please check your credentials and try again.')
        sys.exit()
    else:
        print('Connected to <' + config['CREDENTIALS']['instance url'] + '>')

    # validate all the parameters for this script
    set_item_ids = get_set_ids()

    if not validate_parameters():
        sys.exit()

    # pull down all the meta data for this instance
    print('Retrieving Instance meta data...')
    get_meta_data()
    print('Successfully retrieved ' + str(len(item_type_map)) + ' item type definitions.')

    # lets validate the user specified set item ids. this script will only work with sets
    if not validate_set_item_ids(set_item_ids):
        print('Invalid set ids, please confirm that these ids are valid items and of type set.')
        sys.exit()
    else:
        print('Specified Set IDs ' + str(set_item_ids) + ' are valid')

    print(str(set_item_ids) + ' sets being processed, each set will be processed sequentially.')
    # loop through the list of set item ids
    for set_item_id in set_item_ids:
        set_item = client.get_item(set_item_id)

        # print out some data aboouot
        print('Processing Set <' + config['CREDENTIALS']['instance url'] + '/perspective.req#/containers/'
              + str(set_item_id)
              + '?projectId='
              + str(set_item.get('project'))
              + '>')

        # lets pull the entire hierarchy under this set
        print('Retrieving all children items from set id: [' + str(set_item_id) + '] ...')
        retrieve_items(set_item_id)
        print('Successfully retrieved ' + str(item_count) + ' items.')

        # create a backup of the data
        if get_create_snapshot():
            print('Saving current state of item in set [' + str(set_item_id) + '] to json file.')
            create_snapshot(set_item_id)

        # get the child item type form the root set
        child_item_type = get_child_item_type(set_item_id)
        # create a temp folder
        temp_folder_id = create_temp_folder(set_item_id, child_item_type)

        with ChargingBar('Processing Items', max=item_count, suffix='%(percent).1f%% - %(eta)ds') as bar:
            process_children_items(set_item_id, temp_folder_id, child_item_type, bar)

        client.delete_item(temp_folder_id)
        reset_globals()

    print('\nScript execution finished')

    # here are some fun stats for nerds
    if get_stats_for_nerds():
        elapsed_time = '%.2f' % (time.time() - start)
        print('total execution time: ' + elapsed_time + ' seconds')
        print('# items converted into folders: ' + str(conversion_count))
        print('# items re-indexed: ' + str(moved_item_count))
