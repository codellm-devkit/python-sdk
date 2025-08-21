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
package com.ibm.websphere.samples.daytrader.web.prims.ejb3;

import java.io.IOException;

import javax.annotation.Resource;
import javax.jms.Connection;
import javax.jms.ConnectionFactory;
import javax.jms.JMSContext;
import javax.jms.TextMessage;
import javax.jms.Topic;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

/**
 * This primitive is designed to run inside the TradeApplication and relies upon
 * the {@link com.ibm.websphere.samples.daytrader.util.TradeConfig} class to set
 * configuration parameters. PingServlet2MDBQueue tests key functionality of a
 * servlet call to a post a message to an MDB Topic. The TradeStreamerMDB (and
 * any other subscribers) receives the message This servlet makes use of the MDB
 * EJB {@link com.ibm.websphere.samples.daytrader.ejb3.DTStreamer3MDB} by
 * posting a message to the MDB Topic
 */
@WebServlet(name = "ejb3.PingServlet2MDBTopic", urlPatterns = { "/ejb3/PingServlet2MDBTopic" })
public class PingServlet2MDBTopic extends HttpServlet {

    private static final long serialVersionUID = 5925470158886928225L;

    private static String initTime;

    private static int hitCount;

    @Resource(name = "jms/TopicConnectionFactory", authenticationType = javax.annotation.Resource.AuthenticationType.APPLICATION)
    private ConnectionFactory topicConnectionFactory;

    // TODO: Glassfish does not like this - change to lookup?
    @Resource(name = "jms/TradeStreamerTopic")
    private Topic tradeStreamerTopic;

    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException, ServletException {

        res.setContentType("text/html");
        java.io.PrintWriter out = res.getWriter();
        // use a stringbuffer to avoid concatenation of Strings
        StringBuffer output = new StringBuffer(100);
        output.append("<html><head><title>PingServlet2MDBTopic</title></head>"
                + "<body><HR><FONT size=\"+2\" color=\"#000066\">PingServlet2MDBTopic<BR></FONT>" + "<FONT size=\"-1\" color=\"#000066\">"
                + "Tests the basic operation of a servlet posting a message to an EJB MDB (and other subscribers) through a JMS Topic.<BR>"
                + "<FONT color=\"red\"><B>Note:</B> Not intended for performance testing.</FONT>");

        // we only want to look up the JMS resources once
        try {

            Connection conn = topicConnectionFactory.createConnection();

            try {
                TextMessage message = null;
                int iter = TradeConfig.getPrimIterations();
                for (int ii = 0; ii < iter; ii++) {
                    /*Session sess = conn.createSession(false, Session.AUTO_ACKNOWLEDGE);
                    try {
                        MessageProducer producer = sess.createProducer(tradeStreamerTopic);
                        message = sess.createTextMessage();

                        String command = "ping";
                        message.setStringProperty("command", command);
                        message.setLongProperty("publishTime", System.currentTimeMillis());
                        message.setText("Ping message for topic java:comp/env/jms/TradeStreamerTopic sent from PingServlet2MDBTopic at " + new java.util.Date());

                        producer.send(message);
                    } finally {
                        sess.close();
                    }*/
                	
                	JMSContext context = topicConnectionFactory.createContext();
            		
            		message = context.createTextMessage();

            		message.setStringProperty("command", "ping");
                    message.setLongProperty("publishTime", System.currentTimeMillis());
                    message.setText("Ping message for topic java:comp/env/jms/TradeStreamerTopic sent from PingServlet2MDBTopic at " + new java.util.Date());
              		
            		context.createProducer().send(tradeStreamerTopic, message);
                }

                // write out the output
                output.append("<HR>initTime: ").append(initTime);
                output.append("<BR>Hit Count: ").append(hitCount++);
                output.append("<HR>Posted Text message to java:comp/env/jms/TradeStreamerTopic topic");
                output.append("<BR>Message: ").append(message);
                output.append("<BR><BR>Message text: ").append(message.getText());
                output.append("<BR><HR></FONT></BODY></HTML>");
                out.println(output.toString());

            } catch (Exception e) {
                Log.error("PingServlet2MDBTopic.doGet(...):exception posting message to TradeStreamerTopic topic");
                throw e;
            } finally {
                conn.close();
            }
        } // this is where I actually handle the exceptions
        catch (Exception e) {
            Log.error(e, "PingServlet2MDBTopic.doGet(...): error");
            res.sendError(500, "PingServlet2MDBTopic.doGet(...): error, " + e.toString());

        }
    }

    @Override
    public String getServletInfo() {
        return "web primitive, configured with trade runtime configs, tests Servlet to Session EJB path";
    }

    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        hitCount = 0;
        initTime = new java.util.Date().toString();
    }

}
