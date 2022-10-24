from http import server
from flask import (
    Flask,
    request,
    jsonify,
    Response,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_stache import render_template
from flask_qrcode import QRcode
import configparser
import json
import mysql.connector
import wallycore as wally
import requests


h2b = wally.hex_to_bytes
b2h = wally.hex_from_bytes

app = Flask(__name__, static_url_path='/static')
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"]
)
qrcode = QRcode(app)

config = configparser.RawConfigParser()
config.read('liquidex.conf')

myHost = config.get('MYSQL', 'host')
myUser = config.get('MYSQL', 'username')
myPasswd = config.get('MYSQL', 'password')
myDatabase = config.get('MYSQL', 'database')
liExplorer = config.get('LIQUID', 'explorer')
liRegistry = config.get('LIQUID', 'registry')
exPort = config.get('LIQUIDEX', 'port')
server_url = config.get('LIQUIDEX', 'url')

@app.route('/.well-known/<path:filename>')
def wellKnownRoute(filename):
    return send_from_directory('{}/well-known/'.format(app.root_path), filename, conditional=True)


def home(): # todo: stats
    data = {}
    return data


@app.route('/api/', methods=['GET'])
@limiter.exempt
def api_home():
    data = about()
    return jsonify(data)


@app.route('/', methods=['GET']) # todo: stats
@limiter.exempt
def url_home():
    data = about()
    return render_template('home', **data)


def add_proposal(proposal):
    # check proposal
    try:
        json_object = json.loads(proposal)
    except ValueError as e:
        return {'result': 'Proposal is not a valid json.'}

    if 'version' not in json_object:
        return {'result': 'Missing version element.'}

    if 'tx' not in json_object:
        return {'result': 'Missing transaction element.'}

    if 'inputs' not in json_object:
        return {'result': 'Missing inputs array.'}

    if 'outputs' not in json_object:
        return {'result': 'Missing outputs array.'}


    version = json_object['version']

    # decode tx
    transaction = wally.tx_from_hex(json_object['tx'], wally.WALLY_TX_FLAG_USE_ELEMENTS | wally.WALLY_TX_FLAG_USE_WITNESS)
    # todo: use all inputs
    input_txid = b2h(wally.tx_get_input_txhash(transaction, 0)[::-1])
    input_vout = wally.tx_get_input_index(transaction, 0)
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = 'INSERT INTO proposal (version, json, tx, sha) VALUES (%s, %s, %s, SHA2(%s, 256))'
    val = (version, proposal, json_object['tx'], proposal)
    mycursor.execute(sql, val)
    proposal_id = mycursor.lastrowid
    for row in json_object['inputs']:
        sql = 'INSERT INTO input (proposal_id, asset, amount, txid, vout) VALUES (%s, %s, %s, %s, %s)'
        if (version == 0):
            value = row['amount']
        else:
            value = row['value']
        val = (proposal_id, row['asset'], value, input_txid, input_vout)
        mycursor.execute(sql, val)
    for row in json_object['outputs']:
        sql = 'INSERT INTO output (proposal_id, asset, amount) VALUES (%s, %s, %s)'
        if (version == 0):
            value = row['amount']
        else:
            value = row['value']
        val = (proposal_id, row['asset'], value)
        mycursor.execute(sql, val)
    mydb.commit()
    mydb.close()

    resolve_all()

    data = {'result': 'ok'}
    return data


@app.route('/api/proposal', methods=['POST'])
@limiter.exempt
def api_proposal():
    proposal = request.data 
    #proposal = request.args.get('proposal')
    data = add_proposal(proposal)
    return jsonify(data)


@app.route('/proposal', methods=['GET', 'POST'])
@limiter.exempt
def url_proposal():
    if request.method == 'POST':
      proposal = request.form['proposal']
      data = add_proposal(proposal)
    else:
      data = {}

    return render_template('proposal', **data)


def check(id, asset):
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    try:
        sql = ' \
            SELECT id, txid, vout \
            FROM input \
            WHERE spent = false \
        '
        mycursor.execute(sql)
        myresult = mycursor.fetchall()
        for x in myresult:
            r = requests.get(f'{liExplorer}/api/tx/{x[1]}/outspend/{x[2]}')
            if r.json()['spent'] == True:
                sql = ' \
                    UPDATE input \
                    SET spent = true \
                    WHERE id = ' + str(x[0])
                mycursor.execute(sql)  
                myresult = mycursor.fetchall()
        mydb.commit()
    except:
        print('no input')

    try:
        sql = ' \
            SELECT proposal.id \
            FROM proposal, input \
            WHERE proposal.id = input.proposal_id \
            AND available = true \
            AND input.spent = true \
        '
        mycursor.execute(sql) 
        myresult = mycursor.fetchall()
        for x in myresult:
            sql = 'UPDATE proposal SET available = false WHERE id = ' + str(x[0])
            mycursor.execute(sql) 
            myresult = mycursor.fetchall()

    except:
        print('no proposal')

    mydb.commit()
    mydb.close()


@app.route('/check', methods=['GET'])
@limiter.exempt
def url_check():
    check(1, 1)
    return jsonify('')


def resolve_asset(asset_id):
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT COUNT(*) \
        FROM asset  \
        WHERE asset = "' + asset_id + '"'
    mycursor.execute(sql) 
    result = mycursor.fetchone()
    if result[0]==0:
        # get info from
        try:
            r = requests.get(f'{liRegistry}/{asset_id}')
            registry_element = r.json()
            sql = ' \
                INSERT INTO asset (asset, ticker, name, website, precision_value) \
                VALUES (%s, %s, %s, %s, %s)'
            val = (asset_id, registry_element['contract']['ticker'], registry_element['contract']['name'], registry_element['contract']['entity']['domain'], int(registry_element['contract']['precision']))
            mycursor.execute(sql, val)
        except:
            pass
    mydb.commit()
    mydb.close()


def resolve_all():
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT asset FROM input \
        UNION \
        SELECT asset FROM output \
    '
    mycursor.execute(sql) 
    myresult = mycursor.fetchall()
    mydb.commit()
    mydb.close()
    for x in myresult:
        resolve_asset(x[0])


def book(id, asset, all):
    check(1, 1)
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT proposal.id, json, input.asset, input.amount, CONCAT(input_asset.ticker," - ",input_asset.name," (",input_asset.website,")") AS name, output.asset, output.amount, CONCAT(output_asset.ticker," - ",output_asset.name," (",output_asset.website,")") AS name, available, proposal.creation_timestamp, input_asset.precision_value, output_asset.precision_value, proposal.version \
        FROM proposal \
        INNER JOIN output \
        ON proposal.id = output.proposal_id \
        INNER JOIN input \
        ON proposal.id = input.proposal_id \
        LEFT JOIN asset AS input_asset \
        ON input.asset = input_asset.asset \
        LEFT JOIN asset AS output_asset \
        ON output.asset = output_asset.asset \
    '
    if all is None:
        sql = sql +  'WHERE available = TRUE ORDER BY proposal.creation_timestamp DESC'
    else:
        sql = sql +  'ORDER BY proposal.creation_timestamp DESC'
    val = ()
    mycursor.execute(sql)
    myresult = mycursor.fetchall()
    mydb.commit()
    mydb.close()
    filtered_data = {}
    for x in myresult:
      in_precision = x[10]
      if in_precision is None:
          in_precision = 0
      out_precision = x[11]
      if out_precision is None:
          out_precision = 0

      if x[0] not in filtered_data:
        filtered_data[x[0]] = {}
        filtered_data[x[0]]['input'] = []
        filtered_data[x[0]]['output'] = []
        in_multi=2^in_precision
        out_multi=2^out_precision
        filtered_data[x[0]]['ratio']=(x[3]/in_multi)/(x[6]/out_multi)
      filtered_data[x[0]]['id'] = x[0]
      filtered_data[x[0]]['json'] = x[1]
      print(len(x[1]))
      filtered_data[x[0]]['qr'] = QRcode.qrcode(f"{server_url}/api/getproposaljson?id={x[0]}")
      filtered_data[x[0]]['available'] = x[8]
      filtered_data[x[0]]['creation_timestamp'] = x[9]
      filtered_data[x[0]]['version'] = x[12]
      in_format = '%.'+str(in_precision)+'f'
      out_format = '%.'+str(out_precision)+'f'
      filtered_data[x[0]]['input'].append({'asset': x[2], 'sats': '%.0f' % x[3], 'amount': in_format % (x[3]/(10**in_precision)), 'name': x[4], 'url': liExplorer})
      filtered_data[x[0]]['output'].append({'asset': x[5], 'sats': '%.0f' % x[6], 'amount': out_format % (x[6]/(10**out_precision)), 'name': x[7], 'url': liExplorer})

      ordered_data={}
      
    return filtered_data


@app.route('/api/book', methods=['GET'])
@limiter.exempt
def api_book():
    id = request.args.get('id')
    asset = request.args.get('asset')
    all = request.args.get('all')
    data = book(id, asset, all)
    return jsonify(data)


@app.route('/api/getproposal', methods=['GET'])
@limiter.exempt
def url_getproposal():
    id = request.args.get('id')
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT json\
        FROM proposal\
        WHERE proposal.id = ' + str(id)
    mycursor.execute(sql)
    myresult = mycursor.fetchone()
    mydb.commit()
    mydb.close()
    return Response(myresult[0],
        mimetype='text/plain',
        headers={'Content-disposition':'attachment; filename=proposal_' + str(id) + '.txt'})

@app.route('/api/getproposaljson', methods=['GET'])
@limiter.exempt
def url_getproposaljson():
    id = request.args.get('id')
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT json\
        FROM proposal\
        WHERE proposal.id = ' + str(id)
    mycursor.execute(sql)
    myresult = mycursor.fetchone()
    mydb.commit()
    mydb.close()
    return myresult[0]

@app.route('/book', methods=['GET'])
@limiter.exempt
def url_book():
    id = request.args.get('id')
    asset = request.args.get('asset')
    all = request.args.get('all')
    filtered_data = book(id, asset, all)

    results = []
    for k in filtered_data.keys():
        results.append(filtered_data[k])

    data = {'results': results}
    return render_template('book', **data)


def about():
    data = {}
    return data


@app.route('/api/about', methods=['GET'])
@limiter.exempt
def api_about():
    data = about()
    return jsonify(data)


@app.route('/about', methods=['GET'])
@limiter.exempt
def url_about():
    data = about()
    return render_template('about', **data)


if __name__ == '__main__':
    app.import_name = '.'
    app.run(host='0.0.0.0', port=exPort)
