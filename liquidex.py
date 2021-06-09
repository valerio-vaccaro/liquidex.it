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
    print(proposal)
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

    # decode tx
    transaction = wally.tx_from_hex(json_object['tx'], wally.WALLY_TX_FLAG_USE_ELEMENTS | wally.WALLY_TX_FLAG_USE_WITNESS)
    # todo: use all inputs
    input_txid = b2h(wally.tx_get_input_txhash(transaction, 0)[::-1])
    input_vout = wally.tx_get_input_index(transaction, 0)
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = 'INSERT INTO proposal (json, tx) VALUES (%s, %s)'
    val = (proposal, json_object['tx'])
    mycursor.execute(sql, val)
    proposal_id = mycursor.lastrowid
    for row in json_object['inputs']:
        sql = 'INSERT INTO input (proposal_id, asset, amount, txid, vout) VALUES (%s, %s, %s, %s, %s)'
        val = (proposal_id, row['asset'], row['amount'], input_txid, input_vout)
        mycursor.execute(sql, val)
    for row in json_object['outputs']:
        sql = 'INSERT INTO output (proposal_id, asset, amount) VALUES (%s, %s, %s)'
        val = (proposal_id, row['asset'], row['amount'])
        mycursor.execute(sql, val)
    mydb.commit()
    mydb.close()

    data = {'result': 'ok'}
    return data


@app.route('/api/proposal', methods=['GET'])
@limiter.exempt
def api_proposal():
    proposal = request.args.get('proposal')
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

@app.route('/check', methods=['GET'])
@limiter.exempt
def url_check():
    check(1, 1)
    return jsonify('')

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
            r = requests.get('https://blockstream.info/liquid/api/tx/'+x[1]+'/outspend/'+str(x[2]))
            if r.json()['spent'] == True:
                sql = 'UPDATE input SET spent = true WHERE id ='+str(x[0])
                mycursor.execute(sql)
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
            sql = 'UPDATE proposal SET available = false WHERE id ='+str(x[0])
            mycursor.execute(sql)
            myresult = mycursor.fetchall()

    except:
        print('no proposal')

    mydb.commit()
    mydb.close()


def book(id, asset):
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT proposal.id, json, input.asset, input.amount, output.asset, output.amount, available, creation_timestamp \
        FROM proposal, input, output \
        WHERE proposal.id = input.proposal_id \
        AND proposal.id = output.proposal_id \
        ORDER BY creation_timestamp DESC \
    '
    val = ()
    mycursor.execute(sql)
    myresult = mycursor.fetchall()
    mydb.commit()
    mydb.close()
    filtered_data = {}
    for x in myresult:
      if x[0] not in filtered_data:
          filtered_data[x[0]] = {}
          filtered_data[x[0]]['input'] = []
          filtered_data[x[0]]['output'] = []
      filtered_data[x[0]]['id'] = x[0]
      filtered_data[x[0]]['json'] = json.loads(x[1])
      filtered_data[x[0]]['available'] = x[6]
      filtered_data[x[0]]['creation_timestamp'] = x[7]
      filtered_data[x[0]]['input'].append({'asset':x[2], 'amount':x[3]})
      filtered_data[x[0]]['output'].append({'asset':x[4], 'amount':x[5]})
    return filtered_data


@app.route('/api/book', methods=['GET'])
@limiter.exempt
def api_book():
    id = request.args.get('id')
    asset = request.args.get('asset')
    data = book(id, asset)
    return jsonify(data)


@app.route('/getproposal', methods=['GET'])
@limiter.exempt
def url_getproposal():
    id = request.args.get('id')
    mydb = mysql.connector.connect(host=myHost, user=myUser, passwd=myPasswd, database=myDatabase)
    mycursor = mydb.cursor()
    sql = ' \
        SELECT json\
        FROM proposal\
        WHERE proposal.id ='+str(id)
    mycursor.execute(sql)
    myresult = mycursor.fetchone()
    mydb.commit()
    mydb.close()
    return Response(myresult[0],
        mimetype='text/plain',
        headers={'Content-disposition':'attachment; filename=proposal_'+str(id)+'.txt'})


@app.route('/book', methods=['GET'])
@limiter.exempt
def url_book():
    id = request.args.get('id')
    asset = request.args.get('asset')
    filtered_data = book(id, asset)

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
    app.run(host='0.0.0.0', port=8155)
