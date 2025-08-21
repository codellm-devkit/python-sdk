/**
 * (C) Copyright IBM Corporation 2015, 2021.
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
package com.ibm.websphere.samples.daytrader.web.websocket;

import java.util.Iterator;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;

import javax.annotation.Priority;
import javax.enterprise.event.ObservesAsync;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.interceptor.Interceptor;
import javax.inject.Inject;
import javax.json.JsonObject;
import javax.websocket.CloseReason;
import javax.websocket.EndpointConfig;
import javax.websocket.OnClose;
import javax.websocket.OnError;
import javax.websocket.OnMessage;
import javax.websocket.OnOpen;
import javax.websocket.Session;
import javax.websocket.server.ServerEndpoint;



import com.ibm.websphere.samples.daytrader.interfaces.MarketSummaryUpdate;
import com.ibm.websphere.samples.daytrader.interfaces.QuotePriceChange;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.RecentQuotePriceChangeList;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;


/** This class is a WebSocket EndPoint that sends the Market Summary in JSON form and
 *  encodes recent quote price changes when requested or when triggered by CDI events.
 **/

@ServerEndpoint(value = "/marketsummary",encoders={QuotePriceChangeListEncoder.class},decoders={ActionDecoder.class})
public class MarketSummaryWebSocket {

  @Inject
  RecentQuotePriceChangeList recentQuotePriceChangeList;
  
  private TradeServices tradeAction;

  private static final List<Session> sessions = new CopyOnWriteArrayList<>();  
  private final CountDownLatch latch = new CountDownLatch(1);

  @Inject 
  public MarketSummaryWebSocket(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  // should never be used
  public MarketSummaryWebSocket(){
  }

  @OnOpen
  public void onOpen(final Session session, EndpointConfig ec) {  
    Log.trace("MarketSummaryWebSocket:onOpen -- session -->" + session + "<--");

    sessions.add(session);
    latch.countDown();
  } 

  @OnMessage
  public void sendMarketSummary(ActionMessage message, Session currentSession) {

    String action = message.getDecodedAction();

    Log.trace("MarketSummaryWebSocket:sendMarketSummary -- received -->" + action + "<--");

    // Make sure onopen is finished
    try { 
      latch.await();
    } catch (Exception e) {
      e.printStackTrace();
      return;
    }
    
    
    if (action != null && action.equals("updateMarketSummary")) {

      try {

        JsonObject mkSummary = tradeAction.getMarketSummary().toJSON();

        Log.trace("MarketSummaryWebSocket:sendMarketSummary -- sending -->" + mkSummary + "<--");
                
        currentSession.getAsyncRemote().sendText(mkSummary.toString());
        
      } catch (Exception e) {
        e.printStackTrace();
      }
    } else if (action != null && action.equals("updateRecentQuotePriceChange")) {
      if (!recentQuotePriceChangeList.isEmpty()) {
        currentSession.getAsyncRemote().sendObject(recentQuotePriceChangeList.recentList());
      }
    }
  }

  @OnError
  public void onError(Throwable t, Session currentSession) {
    Log.trace("MarketSummaryWebSocket:onError -- session -->" + currentSession + "<--");
    t.printStackTrace();
  }

  @OnClose
  public void onClose(Session session, CloseReason reason) {
    Log.trace("MarketSummaryWebSocket:onClose -- session -->" + session + "<--");
    sessions.remove(session);
  }

  public void onStockChange(@ObservesAsync @Priority(Interceptor.Priority.APPLICATION) @QuotePriceChange String event) {

    Log.trace("MarketSummaryWebSocket:onStockChange");

    Iterator<Session> failSafeIterator = sessions.iterator();
    while(failSafeIterator.hasNext()) {
      Session s = failSafeIterator.next();
      if (s.isOpen()) {
        s.getAsyncRemote().sendObject(recentQuotePriceChangeList.recentList());
      }
    }
  }

  public void onMarketSummarytUpdate(@ObservesAsync @Priority(Interceptor.Priority.APPLICATION) @MarketSummaryUpdate String event) {

    Log.trace("MarketSummaryWebSocket:onJMSMessage");
   
    try {
    JsonObject mkSummary = tradeAction.getMarketSummary().toJSON();
    
    Iterator<Session> failSafeIterator = sessions.iterator();
    while(failSafeIterator.hasNext()) {
      Session s = failSafeIterator.next();
      if (s.isOpen()) {
        s.getAsyncRemote().sendText(mkSummary.toString());
      }
    }
    } catch (Exception e) {
      e.printStackTrace();
    }
  }
}
