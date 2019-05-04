OPTIONAL: You can choose to integrate with [Pushover](https://pushover.net) or [AWS SNS](https://aws.amazon.com/sns/) to get a push notification to your phone when the copy process is done. Depending on your wireless network speed/connection, copying files may take some time, so a push notification can help confirm that the process finished. If no files were copied (i.e. all manually saved dashcam files were already copied, no notification will be sent.).

The Pushover service is free for up to 7,500 messages per month, but the [iOS](https://pushover.net/clients/ios)/[Android](https://pushover.net/clients/android) apps do have a one time cost, after a free trial period.

You can also choose to send notification through AWS SNS. You can create a free AWS account and the free tier enables you to receive notifications via SNS for free.

*This also assumes your Pi is connected to a network with internet access.*

*Pushover*
1. Create a free account at Pushover.net, and install and log into the mobile Pushover app. 
1. On the Pushover dashboard on the web, copy your **User key**. 
1. [Create a new Application](https://pushover.net/apps/build) at Pushover.net. The description and icon don't matter, choose what you prefer. 
1. Copy the **Application Key** for the application you just created. The User key + Application Key are basically a username/password combination to needed to send the push. 
1. Run these commands, substituting your user key and app key in the appropriate places. No `"` are needed. 
    ```
    export pushover_enabled=true
    export pushover_user_key=put_your_userkey_here
    export pushover_app_key=put_your_appkey_here
    ```

*AWS SNS*
1. Create a free account at AWS.
1. Create a user in IAM and give it the rights to SNS.
1. Create a new SNS topic.
1. Create the notification end point (email or other)
1. Run these commands, substituting your user key and app key in the appropriate places. Use of `"` is required for aws_sns_topic_arn. 
    ```
    export sns_enabled=true
    export aws_region=us-east-1
    export aws_access_key_id=put_your_accesskeyid_here
    export aws_secret_key=put_your_secretkey_here
    export aws_sns_topic_arn=put_your_sns_topicarn_here
    ```

