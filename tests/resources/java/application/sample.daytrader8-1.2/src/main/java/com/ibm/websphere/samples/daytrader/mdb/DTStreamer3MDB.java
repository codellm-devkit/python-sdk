/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.mdb;

import javax.annotation.Resource;
import javax.ejb.ActivationConfigProperty;
import javax.ejb.MessageDriven;
import javax.ejb.MessageDrivenContext;
import javax.ejb.TransactionAttribute;
import javax.ejb.TransactionAttributeType;
import javax.ejb.TransactionManagement;
import javax.ejb.TransactionManagementType;
import javax.jms.Message;
import javax.jms.MessageListener;
import javax.jms.TextMessage;


import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.MDBStats;
import com.ibm.websphere.samples.daytrader.util.TimerStat;

//For Glassfish - take jms/ off of the destination name

@TransactionAttribute(TransactionAttributeType.REQUIRED)
@TransactionManagement(TransactionManagementType.CONTAINER)
@MessageDriven(activationConfig = { @ActivationConfigProperty(propertyName = "acknowledgeMode", propertyValue = "Auto-acknowledge"),
    @ActivationConfigProperty(propertyName = "destinationType", propertyValue = "javax.jms.Topic"),
    @ActivationConfigProperty(propertyName = "destination", propertyValue = "jms/TradeStreamerTopic"),
    @ActivationConfigProperty(propertyName = "subscriptionDurability", propertyValue = "NonDurable") })
@Trace
public class DTStreamer3MDB implements MessageListener {

  private final MDBStats mdbStats;
  private int statInterval = 10000;

  @Resource
  public MessageDrivenContext mdc;
 

  /** Creates a new instance of TradeSteamerMDB */
  public DTStreamer3MDB() {
    Log.trace("DTStreamer3MDB:DTStreamer3MDB()");
    
    if (statInterval <= 0) {
      statInterval = 10000;
    }
    mdbStats = MDBStats.getInstance();
  }

  @Override
  public void onMessage(Message message) {

    try {
      Log.trace("DTStreamer3MDB:onMessage -- received message -->" + ((TextMessage) message).getText() + "command-->"
          + message.getStringProperty("command") + "<--");
      
      String command = message.getStringProperty("command");
      if (command == null) {
        Log.debug("DTStreamer3MDB:onMessage -- received message with null command. Message-->" + message);
        return;
      }
      if (command.equalsIgnoreCase("updateQuote")) {
        Log.trace("DTStreamer3MDB:onMessage -- received message -->" + ((TextMessage) message).getText() + "\n\t symbol = "
              + message.getStringProperty("symbol") + "\n\t current price =" + message.getStringProperty("price") + "\n\t old price ="
              + message.getStringProperty("oldPrice"));
        
        long publishTime = message.getLongProperty("publishTime");
        long receiveTime = System.currentTimeMillis();

        TimerStat currentStats = mdbStats.addTiming("DTStreamer3MDB:udpateQuote", publishTime, receiveTime);

        if ((currentStats.getCount() % statInterval) == 0) {
          Log.log(" DTStreamer3MDB: " + statInterval + " prices updated:" +
              " Total message count = " + currentStats.getCount() +
              " Time (in seconds):" +
              " min: " +currentStats.getMinSecs()+
              " max: " +currentStats.getMaxSecs()+
              " avg: " +currentStats.getAvgSecs() );
        }
      } else if (command.equalsIgnoreCase("ping")) {
        Log.trace("DTStreamer3MDB:onMessage  received ping command -- message: " + ((TextMessage) message).getText());
        

        long publishTime = message.getLongProperty("publishTime");
        long receiveTime = System.currentTimeMillis();

        TimerStat currentStats = mdbStats.addTiming("DTStreamer3MDB:ping", publishTime, receiveTime);

        if ((currentStats.getCount() % statInterval) == 0) {
          Log.log(" DTStreamer3MDB: received " + statInterval + " ping messages." +
              " Total message count = " + currentStats.getCount() +
              " Time (in seconds):" +
              " min: " +currentStats.getMinSecs()+
              " max: " +currentStats.getMaxSecs()+
              " avg: " +currentStats.getAvgSecs());
        }
      } else {
        Log.error("DTStreamer3MDB:onMessage - unknown message request command-->" + command + "<-- message=" + ((TextMessage) message).getText());
      }
    } catch (Throwable t) {
      // JMS onMessage should handle all exceptions
      Log.error("DTStreamer3MDB: Exception", t);
      //UPDATE - Not rolling back for now -- so error messages are not redelivered
      mdc.setRollbackOnly();
    }
  }

}
