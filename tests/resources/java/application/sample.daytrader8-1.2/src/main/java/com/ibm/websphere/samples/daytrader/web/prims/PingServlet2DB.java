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

import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.impl.direct.TradeDirect;
import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingServlet2DB tests the path of a servlet making a JDBC connection to a
 * database
 *
 */

@WebServlet(name = "PingServlet2DB", urlPatterns = { "/servlet/PingServlet2DB" })
public class PingServlet2DB extends HttpServlet {

    private static final long serialVersionUID = -6456675185605592049L;
    private static String initTime;
    private static int hitCount;

    /**
     * forwards post requests to the doGet method Creation date: (11/6/2000
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        res.setContentType("text/html");
        java.io.PrintWriter out = res.getWriter();
        String symbol = null;
        StringBuffer output = new StringBuffer(100);

        try {
            // TradeJDBC uses prepared statements so I am going to make use of
            // it's code.
            TradeDirect trade = new TradeDirect();
            trade.getConnPublic();

            output.append("<html><head><title>PingServlet2DB.</title></head>"
                    + "<body><HR><FONT size=\"+2\" color=\"#000066\">PingServlet2DB:</FONT><HR><FONT size=\"-1\" color=\"#000066\">Init time : " + initTime);
            hitCount++;
            output.append("<BR>Hit Count: " + hitCount);
            output.append("<HR></body></html>");
            out.println(output.toString());
        } catch (Exception e) {
            Log.error(e, "PingServlet2DB -- error getting connection to the database", symbol);
            res.sendError(500, "PingServlet2DB Exception caught: " + e.toString());
        }
    }

    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/
    @Override
    public String getServletInfo() {
        return "Basic JDBC Read using a prepared statment, makes use of TradeJDBC class";
    }

    /**
     * called when the class is loaded to initialize the servlet
     *
     * @param config
     *            ServletConfig:
     **/
    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        hitCount = 0;
        initTime = new java.util.Date().toString();
    }
}