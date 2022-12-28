import os
import logging
import sys
import cfnresponse
import boto3
import uuid
from datetime import datetime
from utils.html_functions import HTML
from utils.ses import SES
from utils.generate_csv import GENCSV
from aws.resources import Resources
from utils.slack_send import Slackalert
from utils.config_parser import parse_config
from utils.config_parser import merges
from utils.config_parser import check_env
from utils.s3_send import uploadDirectory
from utils.filemanager import FileManager
from utils.generate_inv import GENINV
from utils.ses_verification import verify_identity
log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

def lambda_handler(event=None, context=None):
    print("Starting PennyPincher")
    
    default_config = parse_config('./utils/default.yaml') 
    overwrite_config = parse_config('./config.yaml') 
    final_config = merges(default_config,overwrite_config)
    resource_config = final_config['resources']
    env_config = final_config['config']['env']
    env_config = check_env(env_config)
    channel_name =  env_config['channel_name']   #Slack Channel Name
    from_address = env_config['from_address']               #SES verified email address from which email is to be sent
    to_address = env_config['to_address']         #Email addresses of recipents (Comma Separated)
    ses_region = env_config['ses_region']                   #Region where SES is configured
    reporting_platform = env_config['reporting_platform']    #Email/Slack/Email and Slack
    account_name = env_config['account_name'] 
    webhook_url = env_config['webhook_url']
    report_bucket = env_config['report_bucket']
    #Verifying Identities
    if 'email' in  reporting_platform.lower().split(','):
        email_addresses = to_address.split(',')
        email_addresses.append(from_address)
        unique_list = set(email_addresses) 
        email_addresses = (list(unique_list))
        response= verify_identity(email_addresses)
        print(response)
    #Report Headerse
    headers_inventory = ['ResourceID','ResouceName','ServiceName','Type','VPC',
                          'State','Region', 'Idle'
                        ]
    headers = ['ResourceID','ResouceName','ServiceName','Type','VPC',
               'State','Region','Finding','EvaluationPeriod (seconds)','Criteria','Saving($)'
              ]

    #For removing any existing loggers in lambda
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            root.removeHandler(handler)
    #Initilizaing logger for error logging
    logging.basicConfig(level=logging.WARNING)
    logger = logging.getLogger()
    
    try:
        print(reporting_platform.lower().split(','))
        resource = Resources(resource_config, headers, headers_inventory)    #Object for generating report
        html_obj = HTML()               #Object for generating html page
        ses_obj = SES(from_address=from_address, to_address=to_address, ses_region=ses_region)    #Object to send email
        slack_obj = Slackalert(channel=channel_name, webhook_url=webhook_url)           #object to send report to slack

        print(html_obj)
        html, resource_info, total_savings, inventory_info = resource.get_report(html_obj, slack_obj)
        print("Total savings: $" + str(round(total_savings, 2)))
        current_datetime=datetime.utcnow().isoformat("T","minutes").replace(":", "-")
        dir_path=f"/tmp/pennypincher_reports/{current_datetime}"
        print(dir_path)
        os.makedirs(dir_path,exist_ok=True)
        html_path = dir_path+ '/pennypincher_findings.html'
        header = '<h3><b>Cost Optimization Report |  ' + account_name + ' | Total Savings: $'+ str(round(total_savings, 2)) + '</h3></b>'
        html = header + html
        with FileManager(html_path, 'w') as f:
            f.write(html)
        print("Findings File is at: pennypincher_findings.html")
        file_name = "/tmp/pennypincher_reports"
        if len(resource_info) > 0:
            csv_obj = GENCSV(resource_info, total_savings, dir_path, current_datetime)
            csv_obj.generate_csv()
            print(f"CSV Report is at: {dir_path} directory")
        if len(inventory_info) > 0 :
            inv_obj = GENINV(inventory_info, dir_path, current_datetime)
            inv_obj.generate_inv()
        if 'email' in  reporting_platform.lower().split(','):
            ses_obj.ses_sendmail(
                sub='Cost Optimization Report | ' + account_name + ' | Total Savings: $'+ str(round(total_savings, 2)),
                html=html)
        ## Sending report in s3   
        if 's3' in  reporting_platform.lower().split(','):
            uploadDirectory(dir_path,report_bucket,current_datetime)
        if 'slack' in  reporting_platform.lower().split(','):
            print("Sending report to slack .....")
            slack_obj.slack_alert(resource_info, account_name, str(round(total_savings, 2)),report_bucket,current_datetime,reporting_platform)      
                 
    except Exception as e:
        logger.error("Error on line {} in main.py".format(sys.exc_info()[-1].tb_lineno) +
                     " | Message: " + str(e))
        sys.exit(1)


def cfnresponsefun(event, context):
    
    '''Redirect to handler func based on RequestType '''
    physical_resource_id = event.get('PhysicalResourceId', 'ssm-%s' % uuid.uuid4().hex)

    try:
        response = None
        print(event)
        if event['RequestType'] == 'Create' or event['RequestType'] == 'Update':
            response = lambda_handler()#Main logic to run, in our case lambda_handler function
            print(response)

        cfnresponse.send(event, context, cfnresponse.SUCCESS, response, physical_resource_id)
        return 'Completed Successfully'

    except Exception as ex:
        log.error("Error: Failed to %s update release info on gateway: %s" % (event['RequestType'], str(ex)))
        cfnresponse.send(event, context,
                        cfnresponse.FAILED,
                        {'Exception': repr(ex)})
        return 'Exception: %s' % str(ex)


if __name__ == "__main__":
    lambda_handler()