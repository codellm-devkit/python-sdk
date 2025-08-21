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

import javax.inject.Inject;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.entities.QuoteDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.TradeEJB;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;


/**
 *
 * PingServlet2TwoPhase tests key functionality of a TwoPhase commit In this
 * primitive a servlet calls a Session EJB which begins a global txn The Session
 * EJB then reads a DB row and sends a message to JMS Queue The txn is closed w/
 * a 2-phase commit
 *
 */
@WebServlet(name = "ejb3.PingServlet2TwoPhase", urlPatterns = { "/ejb3/PingServlet2TwoPhase" })
public class PingServlet2TwoPhase extends HttpServlet {

  private static final long serialVersionUID = -1563251786527079548L;

  private static String initTime;

  private static int hitCount;

  @Inject
  @TradeEJB
  private TradeServices tradeSLSBLocal;

  @Override
  public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
    doGet(req, res);
  }



  @Override
  public void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException, ServletException {

    res.setContentType("text/html");
    java.io.PrintWriter out = res.getWriter();
    String symbol = null;
    QuoteDataBean quoteData = null;
    StringBuffer output = new StringBuffer(100);

    output.append("<html><head><title>PingServlet2TwoPhase</title></head>"
        + "<body><HR><FONT size=\"+2\" color=\"#000066\">PingServlet2TwoPhase<BR></FONT>" + "<FONT size=\"-1\" color=\"#000066\">"
        + "PingServlet2TwoPhase tests the path of a Servlet calling a Session EJB "
        + "which in turn calls an Entity EJB to read a DB row (quote). The Session EJB " + "then posts a message to a JMS Queue. "
        + "<BR> These operations are wrapped in a 2-phase commit<BR>");

    try {

      try {
        int iter = TradeConfig.getPrimIterations();
        for (int ii = 0; ii < iter; ii++) {
          symbol = TradeConfig.rndSymbol();
          // getQuote will call findQuote which will instaniate the
          // Quote Entity Bean
          // and then will return a QuoteObject
          quoteData = tradeSLSBLocal.pingTwoPhase(symbol);

        }
      } catch (Exception ne) {
        Log.error(ne, "PingServlet2TwoPhase.goGet(...): exception getting QuoteData through Trade");
        throw ne;
      }

      output.append("<HR>initTime: " + initTime).append("<BR>Hit Count: " + hitCount++);
      output.append("<HR>Two phase ping selected a quote and sent a message to TradeBrokerQueue JMS queue<BR>Quote Information<BR><BR>"
          + quoteData.toHTML());
      out.println(output.toString());

    } catch (Exception e) {
      Log.error(e, "PingServlet2TwoPhase.doGet(...): General Exception caught");
      res.sendError(500, "General Exception caught, " + e.toString());
    }
  }

  @Override
  public String getServletInfo() {
    return "web primitive, tests Servlet to Session to Entity EJB and JMS -- 2-phase commit path";

  }

  @Override
  public void init(ServletConfig config) throws ServletException {
    super.init(config);
    hitCount = 0;
    initTime = new java.util.Date().toString();
  }
}