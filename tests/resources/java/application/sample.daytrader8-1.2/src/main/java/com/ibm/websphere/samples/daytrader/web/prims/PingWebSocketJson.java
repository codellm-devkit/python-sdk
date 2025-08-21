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

package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;

import javax.enterprise.concurrent.ManagedThreadFactory;
import javax.naming.InitialContext;
import javax.naming.NamingException;
import javax.websocket.CloseReason;
import javax.websocket.EndpointConfig;
import javax.websocket.OnClose;
import javax.websocket.OnError;
import javax.websocket.OnMessage;
import javax.websocket.OnOpen;
import javax.websocket.Session;
import javax.websocket.server.ServerEndpoint;

import com.ibm.websphere.samples.daytrader.web.websocket.JsonDecoder;
import com.ibm.websphere.samples.daytrader.web.websocket.JsonEncoder;
import com.ibm.websphere.samples.daytrader.web.websocket.JsonMessage;

/** This class a simple websocket that sends the number of times it has been pinged. */

@ServerEndpoint(value = "/pingWebSocketJson",encoders=JsonEncoder.class ,decoders=JsonDecoder.class)
public class PingWebSocketJson {

    private Session currentSession = null;
    private Integer sentHitCount = null;
    private Integer receivedHitCount = null;
       
    @OnOpen
    public void onOpen(final Session session, EndpointConfig ec) {
        currentSession = session;
        sentHitCount = 0;
        receivedHitCount = 0;
        
        
        InitialContext context;
        ManagedThreadFactory mtf = null;
        
        try {
            context = new InitialContext();
            mtf = (ManagedThreadFactory) context.lookup("java:comp/DefaultManagedThreadFactory");
        
        } catch (NamingException e1) {
            // TODO Auto-generated catch block
            e1.printStackTrace();
        }
        
        Thread thread = mtf.newThread(new Runnable() {

            @Override
            public void run() {
                
                try {
                
                    Thread.sleep(500);
                    
                    while (currentSession.isOpen()) {
                        sentHitCount++;
                    
                        JsonMessage response = new JsonMessage();
                        response.setKey("sentHitCount");
                        response.setValue(sentHitCount.toString());
                        currentSession.getAsyncRemote().sendObject(response);

                        Thread.sleep(100);
                    }
                    
                           
                } catch (InterruptedException e) {
                    e.printStackTrace();
                }
            }
                
        });
        
        thread.start();
        
    }

    @OnMessage
    public void ping(JsonMessage message) throws IOException {
        receivedHitCount++;
        JsonMessage response = new JsonMessage();
        response.setKey("receivedHitCount");
        response.setValue(receivedHitCount.toString());
        currentSession.getAsyncRemote().sendObject(response);
    }

    @OnError
    public void onError(Throwable t) {
        t.printStackTrace();
    }

    @OnClose
    public void onClose(Session session, CloseReason reason) {
       
    }

}
